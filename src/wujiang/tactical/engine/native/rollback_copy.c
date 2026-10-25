/* CPython container-copy kernel. Keep the GIL and original object values.
 * Custom types/deques use the original Python copier, including its hooks.
 * No memo: shared containers are independently copied at every occurrence.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#ifndef WUJIANG_NATIVE_SOURCE_ID
#error Build through tools/build_ai_native.py
#endif

static PyObject *copy_value(PyObject *, PyObject *, PyObject *, int);
static PyObject *copy_flat_value(PyObject *, PyObject *, PyObject *, int);
/* Diagnostic counts only; the GIL serializes access, no battle state stored. */
static unsigned long long flat_counts[3] = {0, 0, 0};
static unsigned long long component_scan_counts[4] = {0, 0, 0, 0};

/* Resolve LOAD_GLOBAL(getattr) afresh on every iteration, including after a
 * custom descriptor/bool callback changes the globals or builtins mapping.
 * Call the builtin with a default instead of manufacturing AttributeError
 * for each ordinary absent flag. No state or callback result is cached. */
static PyObject *component_getattr(PyObject *owner, PyObject *name, PyObject *fallback,
                                   PyObject *globals, PyObject *builtins, PyObject *lookup_name) {
    PyObject *lookup = PyDict_GetItemWithError(globals, lookup_name);
    if (lookup == NULL && !PyErr_Occurred()) lookup = PyDict_GetItemWithError(builtins, lookup_name);
    if (lookup == NULL) {
        if (!PyErr_Occurred()) PyErr_SetString(PyExc_NameError, "name 'getattr' is not defined");
        return NULL;
    }
    Py_INCREF(lookup);
    PyObject *args[] = {owner, name, fallback};
    PyObject *value = PyObject_Vectorcall(lookup, args, 3, NULL);
    Py_DECREF(lookup);
    return value;
}

static void component_generator_stop_iteration(void) {
    /* Only a flag getter executes inside the reference generator expression.
     * Its StopIteration becomes RuntimeError (PEP 479). A truth-test failure
     * occurs in builtin any(), outside the generator, and is not translated. */
    PyObject *type, *value, *traceback;
    PyErr_Fetch(&type, &value, &traceback);
    PyErr_NormalizeException(&type, &value, &traceback);
    if (value != NULL && traceback != NULL) PyException_SetTraceback(value, traceback);
    PyObject *error = PyObject_CallFunction(PyExc_RuntimeError, "s", "generator raised StopIteration");
    if (error != NULL) {
        if (value != NULL) {
            PyException_SetCause(error, Py_NewRef(value));
            PyException_SetContext(error, Py_NewRef(value));
        }
        PyErr_SetObject(PyExc_RuntimeError, error);
        Py_DECREF(error);
    }
    Py_XDECREF(type);
    Py_XDECREF(value);
    Py_XDECREF(traceback);
}

static PyObject *component_scan_entry(PyObject *self, PyObject *const *args, Py_ssize_t nargs) {
    if (nargs != 4 || !PyDict_CheckExact(args[1]) || !PyDict_CheckExact(args[2])
            || !PyTuple_CheckExact(args[3]) || PyTuple_GET_SIZE(args[3]) != 3) {
        PyErr_SetString(PyExc_TypeError, "component scan requires owner, globals, builtins and three names");
        return NULL;
    }
    PyObject *owner = args[0], *globals = args[1], *builtins = args[2], *names = args[3];
    for (int index = 0; index < 3; index++) {
        if (!PyUnicode_CheckExact(PyTuple_GET_ITEM(names, index))) {
            PyErr_SetString(PyExc_TypeError, "component scan names must be exact strings");
            return NULL;
        }
    }
    PyObject *empty = PyTuple_New(0), *statuses = NULL, *iterator = NULL, *status = NULL;
    if (empty == NULL) return NULL;
    component_scan_counts[0]++;
    statuses = component_getattr(owner, PyTuple_GET_ITEM(names, 0), empty,
                                globals, builtins, PyTuple_GET_ITEM(names, 2));
    Py_CLEAR(empty);
    if (statuses == NULL) goto error;
    iterator = PyObject_GetIter(statuses);
    Py_CLEAR(statuses);
    if (iterator == NULL) goto error;
    int truth = 0;
    while (1) {
        PyObject *next = PyIter_Next(iterator);
        if (next == NULL) {
            if (PyErr_Occurred()) goto error;
            break;
        }
        /* The generator's previous status remains alive until the next
         * element has been fetched and assigned. Preserve that order. */
        Py_XSETREF(status, next);
        component_scan_counts[1]++;
        PyObject *flag = component_getattr(status, PyTuple_GET_ITEM(names, 1), Py_False,
                                          globals, builtins, PyTuple_GET_ITEM(names, 2));
        if (flag == NULL) {
            if (PyErr_ExceptionMatches(PyExc_StopIteration)) component_generator_stop_iteration();
            goto error;
        }
        truth = PyObject_IsTrue(flag);
        Py_DECREF(flag);
        if (truth < 0) goto error;
        if (truth) {
            component_scan_counts[2]++;
            break;
        }
    }
    Py_DECREF(iterator);
    Py_XDECREF(status);
    return PyBool_FromLong(truth);
error:
    component_scan_counts[3]++;
    Py_XDECREF(empty);
    Py_XDECREF(statuses);
    Py_XDECREF(iterator);
    Py_XDECREF(status);
    return NULL;
}

static PyObject *component_scan_statistics(PyObject *self, PyObject *unused) {
    return Py_BuildValue("{s:K,s:K,s:K,s:K}", "calls", component_scan_counts[0],
                         "statuses", component_scan_counts[1], "short_circuits", component_scan_counts[2],
                         "errors", component_scan_counts[3]);
}

/* The Pickler still visits every object and performs its normal full graph
 * serialization. Only the identity-only persistent_id callback moves to C.
 * In particular it must run BEFORE pickle memo lookup, so even a second
 * reference to an already memoized RNG is rejected. No RNG methods run. */
typedef struct {
    PyObject_HEAD
    PyObject *rng, *marker;
    unsigned long long references, visits;
} RngMask;

static int rng_mask_traverse(RngMask *self, visitproc visit, void *arg) {
    Py_VISIT(self->rng);
    Py_VISIT(self->marker);
    return 0;
}

static int rng_mask_clear(RngMask *self) {
    Py_CLEAR(self->rng);
    Py_CLEAR(self->marker);
    return 0;
}

static void rng_mask_dealloc(RngMask *self) {
    PyObject_GC_UnTrack(self);
    rng_mask_clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *rng_mask_persistent_id(RngMask *self, PyObject *obj) {
    self->visits++;
    if (obj != self->rng) Py_RETURN_NONE;
    self->references++;
    if (self->references != 1) {
        PyErr_SetString(PyExc_ValueError,
                        "An RNG alias can bypass Battle.chess_rng dependency tracking");
        return NULL;
    }
    return Py_NewRef(self->marker);
}

static PyObject *rng_mask_references(RngMask *self, void *unused) {
    return PyLong_FromUnsignedLongLong(self->references);
}

static PyObject *rng_mask_visits(RngMask *self, void *unused) {
    return PyLong_FromUnsignedLongLong(self->visits);
}

static PyMethodDef rng_mask_methods[] = {
    {"persistent_id", (PyCFunction)rng_mask_persistent_id, METH_O,
     "Mask exactly one RNG reference; reject a second occurrence."},
    {NULL, NULL, 0, NULL}
};

static PyGetSetDef rng_mask_getset[] = {
    {"references", (getter)rng_mask_references, NULL, NULL, NULL},
    {"visits", (getter)rng_mask_visits, NULL, NULL, NULL},
    {NULL, NULL, NULL, NULL, NULL}
};

static PyTypeObject RngMaskType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "_wujiang_rollback_native.RngMask",
    .tp_basicsize = sizeof(RngMask),
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
    .tp_dealloc = (destructor)rng_mask_dealloc,
    .tp_traverse = (traverseproc)rng_mask_traverse,
    .tp_clear = (inquiry)rng_mask_clear,
    .tp_methods = rng_mask_methods,
    .tp_getset = rng_mask_getset,
};

static PyObject *new_rng_mask(PyObject *self, PyObject *args) {
    PyObject *rng, *marker;
    if (!PyArg_ParseTuple(args, "OO:new_rng_mask", &rng, &marker)) return NULL;
    RngMask *mask = PyObject_GC_New(RngMask, &RngMaskType);
    if (mask == NULL) return NULL;
    mask->rng = Py_NewRef(rng);
    mask->marker = Py_NewRef(marker);
    mask->references = mask->visits = 0;
    PyObject_GC_Track(mask);
    return (PyObject *)mask;
}

static int scalar(PyObject *value) {
    PyTypeObject *t = Py_TYPE(value);
    return value == Py_None || t == &PyBool_Type || t == &PyLong_Type ||
           t == &PyFloat_Type || t == &PyUnicode_Type || t == &PyBytes_Type;
}

static PyObject *copy_container(PyObject *value, PyObject *fallback, PyObject *scalars, int depth, int flat) {
    PyObject *result = NULL, *iterator = NULL, *item = NULL;
    int dictionary = PyDict_CheckExact(value), tuple = PyTuple_CheckExact(value);
    if (dictionary) {
        /* Use the real items iterator: Python callbacks may mutate the source.
         * PyDict_Next would silently miss the original size-change error. */
        PyObject *items = PyObject_CallMethod(value, "items", NULL);
        if (items == NULL) return NULL;
        iterator = PyObject_GetIter(items);
        Py_DECREF(items);
        result = PyDict_New();
    } else {
        iterator = PyObject_GetIter(value);
        result = PyList_New(0);
    }
    if (iterator == NULL || result == NULL) goto error;
    while ((item = PyIter_Next(iterator)) != NULL) {
        PyObject *original = dictionary ? PyTuple_GET_ITEM(item, 1) : item;
        PyObject *copied = flat ? copy_flat_value(original, fallback, scalars, depth + 1)
                                : copy_value(original, fallback, scalars, depth + 1);
        if (copied == NULL) goto error;
        int status = dictionary ? PyDict_SetItem(result, PyTuple_GET_ITEM(item, 0), copied)
                                : PyList_Append(result, copied);
        Py_DECREF(copied);
        if (status < 0) goto error;
        Py_CLEAR(item);
    }
    if (PyErr_Occurred()) goto error;
    Py_DECREF(iterator);
    if (tuple) {
        PyObject *converted = PyList_AsTuple(result);
        Py_DECREF(result);
        return converted;
    }
    return result;
error:
    Py_XDECREF(item);
    Py_XDECREF(iterator);
    Py_XDECREF(result);
    return NULL;
}

static PyObject *copy_fallback(PyObject *value, PyObject *fallback, PyObject *scalars) {
    /* This is the original parent comprehension's membership operation.
     * It must run even for custom metaclasses that spoof a scalar class. */
    int contained = PySet_Contains(scalars, (PyObject *)Py_TYPE(value));
    if (contained < 0) return NULL;
    if (contained) return Py_NewRef(value);
    return PyObject_CallOneArg(fallback, value);
}

static PyObject *copy_value(PyObject *value, PyObject *fallback, PyObject *scalars, int depth) {
    if (scalar(value)) return Py_NewRef(value);
    /* Bound native recursion; cyclic/deep input returns to Python's existing
     * RecursionError handling. Never dereference an unchecked container type. */
    if (depth >= 32) return copy_fallback(value, fallback, scalars);
    if (PySet_CheckExact(value)) return PySet_New(value);
    if (!PyDict_CheckExact(value) && !PyList_CheckExact(value) && !PyTuple_CheckExact(value))
        return copy_fallback(value, fallback, scalars);
    if (Py_EnterRecursiveCall(" while copying battle probe state")) return NULL;
    PyObject *result = copy_container(value, fallback, scalars, depth, 0);
    Py_LeaveRecursiveCall();
    return result;
}

/* No user callback can run in this scan: exact containers, exact string keys
 * and exact scalar values only. Keep the GIL. Mixed/custom containers still
 * use the original live iterator; never shallow-copy their initial contents.
 * In particular a custom copy callback can change a later value or key. */
static int flat_scalars(PyObject *value) {
    if (PyDict_CheckExact(value)) {
        Py_ssize_t position = 0;
        PyObject *key, *item;
        while (PyDict_Next(value, &position, &key, &item))
            if (!PyUnicode_CheckExact(key) || !scalar(item)) return 0;
        return 1;
    }
    if (PyList_CheckExact(value) || PyTuple_CheckExact(value)) {
        Py_ssize_t size = Py_SIZE(value);
        for (Py_ssize_t index = 0; index < size; index++) {
            PyObject *item = PyList_CheckExact(value) ? PyList_GET_ITEM(value, index)
                                                    : PyTuple_GET_ITEM(value, index);
            if (!scalar(item)) return 0;
        }
        return 1;
    }
    return 0;
}

static PyObject *copy_flat_value(PyObject *value, PyObject *fallback, PyObject *scalars, int depth) {
    if (scalar(value)) return Py_NewRef(value);
    if (depth >= 32) return copy_fallback(value, fallback, scalars);
    if (PySet_CheckExact(value)) return PySet_New(value);
    if (!PyDict_CheckExact(value) && !PyList_CheckExact(value) && !PyTuple_CheckExact(value))
        return copy_fallback(value, fallback, scalars);
    if (Py_EnterRecursiveCall(" while copying battle probe state")) return NULL;
    PyObject *result = NULL;
    if (!flat_scalars(value)) {
        result = copy_container(value, fallback, scalars, depth, 1);
    } else if (PyDict_CheckExact(value)) {
        result = PyDict_Copy(value);
        if (result != NULL) flat_counts[0]++;
    } else if (PyList_CheckExact(value)) {
        result = PyList_GetSlice(value, 0, PyList_GET_SIZE(value));
        if (result != NULL) flat_counts[1]++;
    } else {
        /* PySequence_Tuple would reuse an input tuple. The original copier
         * constructs a new nonempty tuple on every occurrence. */
        Py_ssize_t size = PyTuple_GET_SIZE(value);
        result = PyTuple_New(size);
        if (result != NULL)
            for (Py_ssize_t index = 0; index < size; index++)
                PyTuple_SET_ITEM(result, index, Py_NewRef(PyTuple_GET_ITEM(value, index)));
        if (result != NULL) flat_counts[2]++;
    }
    Py_LeaveRecursiveCall();
    return result;
}

static PyObject *copy_entry(PyObject *self, PyObject *args) {
    PyObject *value, *fallback, *scalars;
    if (!PyArg_ParseTuple(args, "OOO:copy_value", &value, &fallback, &scalars)) return NULL;
    if (!PyCallable_Check(fallback)) {
        PyErr_SetString(PyExc_TypeError, "fallback must be callable");
        return NULL;
    }
    if (!PyFrozenSet_CheckExact(scalars)) {
        PyErr_SetString(PyExc_TypeError, "scalar classes must be an exact frozenset");
        return NULL;
    }
    return copy_value(value, fallback, scalars, 0);
}

static PyObject *copy_flat_entry(PyObject *self, PyObject *args) {
    PyObject *value, *fallback, *scalars;
    if (!PyArg_ParseTuple(args, "OOO:copy_flat_value", &value, &fallback, &scalars)) return NULL;
    if (!PyCallable_Check(fallback)) {
        PyErr_SetString(PyExc_TypeError, "fallback must be callable");
        return NULL;
    }
    if (!PyFrozenSet_CheckExact(scalars)) {
        PyErr_SetString(PyExc_TypeError, "scalar classes must be an exact frozenset");
        return NULL;
    }
    return copy_flat_value(value, fallback, scalars, 0);
}

static PyObject *flat_statistics(PyObject *self, PyObject *unused) {
    return Py_BuildValue("{s:K,s:K,s:K}", "dicts", flat_counts[0],
                         "lists", flat_counts[1], "tuples", flat_counts[2]);
}

/* Object discovery/filtering is intentionally done by the original Python
 * collector. User runtime_children, dataclass and namespace protocols remain
 * observable in their original order. Only the capture/restore loops move. */
static PyObject *capture_entry(PyObject *self, PyObject *args) {
    PyObject *objects, *namespace_plan, *fallback_state, *fallback_copy, *scalars, *namespace_copy;
    if (!PyArg_ParseTuple(args, "OOOOOO:capture", &objects, &namespace_plan,
                          &fallback_state, &fallback_copy, &scalars, &namespace_copy)) return NULL;
    PyObject *iterator = PyObject_GetIter(objects), *saved = PyList_New(0), *obj = NULL;
    if (iterator == NULL || saved == NULL) goto error;
    while ((obj = PyIter_Next(iterator)) != NULL) {
        PyObject *planned = PyObject_CallOneArg(namespace_plan, obj);
        if (planned == NULL) goto error;
        int fast = PyObject_IsTrue(planned);
        Py_DECREF(planned);
        if (fast < 0) goto error;
        PyObject *snapshot = NULL;
        if (fast) {
            PyObject *namespace = PyObject_GetAttrString(obj, "__dict__");
            if (namespace == NULL) goto error;
            if (PyDict_CheckExact(namespace))
                snapshot = copy_value(namespace, fallback_copy, scalars, 0);
            else
                snapshot = PyObject_CallOneArg(namespace_copy, namespace);
            Py_DECREF(namespace);
        } else {
            snapshot = PyObject_CallOneArg(fallback_state, obj);
        }
        if (snapshot == NULL) goto error;
        PyObject *pair = PyTuple_Pack(2, obj, snapshot);
        Py_DECREF(snapshot);
        if (pair == NULL) goto error;
        int status = PyList_Append(saved, pair);
        Py_DECREF(pair);
        if (status < 0) goto error;
        Py_CLEAR(obj);
    }
    if (PyErr_Occurred()) goto error;
    Py_DECREF(iterator);
    return saved;
error:
    Py_XDECREF(obj);
    Py_XDECREF(iterator);
    Py_XDECREF(saved);
    return NULL;
}

static PyObject *restore_entry(PyObject *self, PyObject *args) {
    PyObject *saved, *namespace_plan, *fallback_restore;
    if (!PyArg_ParseTuple(args, "OOO:restore", &saved, &namespace_plan, &fallback_restore)) return NULL;
    if (!PyList_CheckExact(saved)) {
        PyErr_SetString(PyExc_TypeError, "checkpoint must be an exact list");
        return NULL;
    }
    for (Py_ssize_t index = PyList_GET_SIZE(saved); index-- > 0;) {
        PyObject *pair = PyList_GET_ITEM(saved, index);
        if (!PyTuple_CheckExact(pair) || PyTuple_GET_SIZE(pair) != 2) {
            PyErr_SetString(PyExc_TypeError, "invalid checkpoint row");
            return NULL;
        }
        PyObject *obj = PyTuple_GET_ITEM(pair, 0), *snapshot = PyTuple_GET_ITEM(pair, 1);
        PyObject *planned = PyObject_CallOneArg(namespace_plan, obj);
        if (planned == NULL) return NULL;
        int fast = PyObject_IsTrue(planned);
        Py_DECREF(planned);
        if (fast < 0) return NULL;
        if (fast) {
            PyObject *namespace = PyObject_GetAttrString(obj, "__dict__");
            if (namespace == NULL) return NULL;
            PyObject *result = PyObject_CallMethod(namespace, "clear", NULL);
            Py_DECREF(namespace);
            if (result == NULL) return NULL;
            Py_DECREF(result);
            /* clear() can release finalizers replacing the instance dict.
             * Re-read it, exactly as the original two Python statements do. */
            namespace = PyObject_GetAttrString(obj, "__dict__");
            if (namespace == NULL) return NULL;
            result = PyObject_CallMethod(namespace, "update", "O", snapshot);
            Py_DECREF(namespace);
            if (result == NULL) return NULL;
            Py_DECREF(result);
        } else {
            PyObject *result = PyObject_CallFunctionObjArgs(fallback_restore, obj, snapshot, NULL);
            if (result == NULL) return NULL;
            Py_DECREF(result);
        }
    }
    Py_RETURN_NONE;
}

static PyMethodDef methods[] = {
    {"component_status_scan", (PyCFunction)component_scan_entry, METH_FASTCALL,
     "Evaluate the original live status predicate with ordered short circuiting."},
    {"component_scan_statistics", component_scan_statistics, METH_NOARGS, "Read component scan work counts."},
    {"new_rng_mask", new_rng_mask, METH_VARARGS, "Create a per-Pickler identity-only RNG alias guard."},
    {"copy_value", copy_entry, METH_VARARGS, "Copy ordered builtin containers with Python fallback."},
    {"copy_flat_value", copy_flat_entry, METH_VARARGS, "Batch-copy exact scalar containers with original fallback."},
    {"flat_copy_statistics", flat_statistics, METH_NOARGS, "Read scalar container copy counts."},
    {"capture", capture_entry, METH_VARARGS, "Capture an ordered object checkpoint."},
    {"restore", restore_entry, METH_VARARGS, "Restore a checkpoint in reverse order."},
    {NULL, NULL, 0, NULL}
};
static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_wujiang_rollback_native", NULL, -1, methods
};
PyMODINIT_FUNC PyInit__wujiang_rollback_native(void) {
    if (PyType_Ready(&RngMaskType) < 0) return NULL;
    PyObject *result = PyModule_Create(&module);
    if (result == NULL) return NULL;
    if (PyModule_AddStringConstant(result, "source_id", WUJIANG_NATIVE_SOURCE_ID) < 0) {
        Py_DECREF(result);
        return NULL;
    }
    return result;
}
