/* Preparation for offline expectation queries only. The production rollback
 * extension is independent and unchanged. Keep the GIL, pointer identity,
 * dictionary order and the Python reference implementation's restore order. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#ifndef WUJIANG_PREDICTION_SOURCE_ID
#error Build through tools/build_ai_prediction_native.py
#endif

typedef struct {
    PyObject *seen, *snapshots, *deque_type;
    unsigned long long visits;
} InputCapture;

static int capture_visit(InputCapture *state, PyObject *value) {
    state->visits++;
    int dictionary = PyDict_CheckExact(value), sequence = PyList_CheckExact(value);
    int deque = (PyObject *)Py_TYPE(value) == state->deque_type;
    int function = PyFunction_Check(value), tuple = PyTuple_CheckExact(value);
    int set = PySet_CheckExact(value);
    /* Scalars/engine objects have no input-container children. Do not allocate
     * visited IDs for these terminal references; the battle graph owns engines. */
    if (!dictionary && !sequence && !deque && !function && !tuple && !set) return 0;
    PyObject *identity = PyLong_FromVoidPtr(value);
    if (identity == NULL) return -1;
    int seen = PySet_Contains(state->seen, identity);
    if (seen < 0 || (!seen && PySet_Add(state->seen, identity) < 0)) {
        Py_DECREF(identity);
        return -1;
    }
    Py_DECREF(identity);
    if (seen) return 0;
    if (Py_EnterRecursiveCall(" while saving prediction inputs")) return -1;
    int result = 0;
    PyObject *saved = NULL, *row = NULL;
    if (function) {
        PyObject *closure = PyFunction_GET_CLOSURE(value);
        if (closure != NULL) {
            for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(closure); i++) {
                PyObject *contents = PyCell_GET(PyTuple_GET_ITEM(closure, i));
                if (contents != NULL && capture_visit(state, contents) < 0) { result = -1; break; }
            }
        }
    } else if (tuple) {
        for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(value); i++) {
            if (capture_visit(state, PyTuple_GET_ITEM(value, i)) < 0) { result = -1; break; }
        }
    } else {
        saved = dictionary ? PyDict_Items(value) : set ? PySet_New(value) : PySequence_List(value);
        if (saved == NULL) { result = -1; goto done; }
        row = PyTuple_Pack(2, value, saved);
        if (row == NULL || PyList_Append(state->snapshots, row) < 0) { result = -1; goto done; }
        /* Like the Python implementation, dictionary keys/set elements are
         * retained but not recursively expanded. All list/deque values are. */
        if (!set) {
            for (Py_ssize_t i = 0; i < PyList_GET_SIZE(saved); i++) {
                PyObject *item = PyList_GET_ITEM(saved, i);
                if (dictionary) item = PyTuple_GET_ITEM(item, 1);
                if (capture_visit(state, item) < 0) { result = -1; break; }
            }
        }
    }
done:
    Py_XDECREF(row);
    Py_XDECREF(saved);
    Py_LeaveRecursiveCall();
    return result;
}

static PyObject *capture_inputs(PyObject *self, PyObject *args) {
    PyObject *callback, *deque_type;
    if (!PyArg_ParseTuple(args, "OO:capture_inputs", &callback, &deque_type)) return NULL;
    if (!PyType_Check(deque_type)) {
        PyErr_SetString(PyExc_TypeError, "deque_type must be a type");
        return NULL;
    }
    InputCapture state = {PySet_New(NULL), PyList_New(0), deque_type, 0};
    if (state.seen == NULL || state.snapshots == NULL || capture_visit(&state, callback) < 0) {
        Py_XDECREF(state.seen);
        Py_XDECREF(state.snapshots);
        return NULL;
    }
    PyObject *result = Py_BuildValue("OK", state.snapshots, state.visits);
    Py_DECREF(state.seen);
    Py_DECREF(state.snapshots);
    return result;
}

/* Read original references directly instead of allocating tuple(map(id,...))
 * for every validation. No __eq__ or user-key hashing is used. */
static int container_matches(PyObject *target, PyObject *saved, PyObject *identities,
                             PyObject *deque_type) {
    if (!PyDict_CheckExact(target) && !PyList_CheckExact(target) && !PySet_CheckExact(target)
            && (PyObject *)Py_TYPE(target) != deque_type) {
        PyErr_SetString(PyExc_TypeError, "unsupported graph container");
        return -1;
    }
    if (!PyTuple_CheckExact(saved)) {
        PyErr_SetString(PyExc_TypeError, "graph saved values must be a tuple");
        return -1;
    }
    Py_ssize_t size = PyObject_Size(target);
    if (size < 0) return -1;
    if (size != PyTuple_GET_SIZE(saved)) return 0;
    if (PyDict_CheckExact(target)) {
        Py_ssize_t pos = 0, i = 0;
        PyObject *key, *value;
        while (PyDict_Next(target, &pos, &key, &value)) {
            PyObject *pair = PyTuple_GET_ITEM(saved, i++);
            if (!PyTuple_CheckExact(pair) || PyTuple_GET_SIZE(pair) != 2) {
                PyErr_SetString(PyExc_TypeError, "invalid saved dictionary row");
                return -1;
            }
            if (key != PyTuple_GET_ITEM(pair, 0) || value != PyTuple_GET_ITEM(pair, 1)) return 0;
        }
        return 1;
    }
    if (PyList_CheckExact(target)) {
        for (Py_ssize_t i = 0; i < size; i++)
            if (PyList_GET_ITEM(target, i) != PyTuple_GET_ITEM(saved, i)) return 0;
        return 1;
    }
    int set = PySet_CheckExact(target);
    if (set && !PyFrozenSet_CheckExact(identities)) {
        PyErr_SetString(PyExc_TypeError, "set identities must be a frozenset");
        return -1;
    }
    PyObject *iterator = PyObject_GetIter(target);
    if (iterator == NULL) return -1;
    PyObject *value;
    Py_ssize_t i = 0;
    int same = 1;
    while ((value = PyIter_Next(iterator)) != NULL) {
        if (set) {
            PyObject *identity = PyLong_FromVoidPtr(value);
            same = identity == NULL ? -1 : PySet_Contains(identities, identity);
            Py_XDECREF(identity);
        } else {
            same = i < PyTuple_GET_SIZE(saved) && value == PyTuple_GET_ITEM(saved, i);
        }
        Py_DECREF(value);
        i++;
        if (same <= 0) break;
    }
    if (PyErr_Occurred()) same = -1;
    Py_DECREF(iterator);
    return same;
}

static PyObject *containers_match(PyObject *self, PyObject *args) {
    PyObject *rows, *deque_type;
    if (!PyArg_ParseTuple(args, "O!O:containers_match", &PyList_Type, &rows, &deque_type)) return NULL;
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(rows); i++) {
        PyObject *row = PyList_GET_ITEM(rows, i);
        if (!PyTuple_CheckExact(row) || PyTuple_GET_SIZE(row) != 3) {
            PyErr_SetString(PyExc_TypeError, "invalid graph row");
            return NULL;
        }
        int same = container_matches(PyTuple_GET_ITEM(row, 0), PyTuple_GET_ITEM(row, 1),
                                     PyTuple_GET_ITEM(row, 2), deque_type);
        if (same < 0) return NULL;
        if (!same) Py_RETURN_FALSE;
    }
    Py_RETURN_TRUE;
}

static int restore_container(PyObject *target, PyObject *saved, PyObject *deque_type) {
    if (PyList_CheckExact(target)) {
        PyObject *slice = PySlice_New(Py_None, Py_None, Py_None);
        if (slice == NULL) return -1;
        int result = PyObject_SetItem(target, slice, saved);
        Py_DECREF(slice);
        return result;
    }
    if (!PyDict_CheckExact(target) && !PySet_CheckExact(target)
            && (PyObject *)Py_TYPE(target) != deque_type) {
        PyErr_SetString(PyExc_TypeError, "unsupported saved input container");
        return -1;
    }
    PyObject *result = PyObject_CallMethod(target, "clear", NULL);
    if (result == NULL) return -1;
    Py_DECREF(result);
    result = PyObject_CallMethod(target, (PyObject *)Py_TYPE(target) == deque_type ? "extend" : "update", "(O)", saved);
    if (result == NULL) return -1;
    Py_DECREF(result);
    return 0;
}

static int input_matches_without_hooks(PyObject *target, PyObject *saved, PyObject *deque_type) {
    /* Only skip writes whose original restore has no user-key hashing. Custom
     * dictionary keys and sets retain the original unconditional protocol. */
    if (PyDict_CheckExact(target) && PyList_CheckExact(saved)) {
        if (PyDict_Size(target) != PyList_GET_SIZE(saved)) return 0;
        Py_ssize_t pos = 0, i = 0;
        PyObject *key, *value;
        while (PyDict_Next(target, &pos, &key, &value)) {
            if (!(PyUnicode_CheckExact(key) || PyLong_CheckExact(key) || PyFloat_CheckExact(key)
                    || PyBytes_CheckExact(key) || key == Py_None || PyBool_Check(key))) return 0;
            PyObject *pair = PyList_GET_ITEM(saved, i++);
            if (!PyTuple_CheckExact(pair) || PyTuple_GET_SIZE(pair) != 2) return 0;
            if (key != PyTuple_GET_ITEM(pair, 0) || value != PyTuple_GET_ITEM(pair, 1)) return 0;
        }
        return 1;
    }
    if (PyList_CheckExact(target) && PyList_CheckExact(saved)) {
        if (PyList_GET_SIZE(target) != PyList_GET_SIZE(saved)) return 0;
        for (Py_ssize_t i = 0; i < PyList_GET_SIZE(saved); i++)
            if (PyList_GET_ITEM(target, i) != PyList_GET_ITEM(saved, i)) return 0;
        return 1;
    }
    if ((PyObject *)Py_TYPE(target) == deque_type && PyList_CheckExact(saved)) {
        Py_ssize_t size = PyObject_Size(target);
        if (size < 0) return -1;
        if (size != PyList_GET_SIZE(saved)) return 0;
        PyObject *iterator = PyObject_GetIter(target), *value;
        if (iterator == NULL) return -1;
        Py_ssize_t i = 0;
        int same = 1;
        while ((value = PyIter_Next(iterator)) != NULL) {
            same = value == PyList_GET_ITEM(saved, i++);
            Py_DECREF(value);
            if (!same) break;
        }
        if (PyErr_Occurred()) same = -1;
        Py_DECREF(iterator);
        return same;
    }
    return 0;
}

static PyObject *restore_rows(PyObject *args, int graph) {
    PyObject *rows, *deque_type;
    int skip_unchanged = 0;
    if (!PyArg_ParseTuple(args, "O!O|p:restore_rows", &PyList_Type, &rows, &deque_type, &skip_unchanged)) return NULL;
    /* Input restore stays unconditional, including custom dictionary-key hash
     * effects. Graph restore skips unchanged containers, as the Python code. */
    for (Py_ssize_t i = PyList_GET_SIZE(rows); i-- > 0;) {
        /* A user dictionary key's hash can run during update(). Hold the row
         * alive across that call; a shortened list ends like reversed(list). */
        if (i >= PyList_GET_SIZE(rows)) break;
        PyObject *row = Py_NewRef(PyList_GET_ITEM(rows, i));
        if (!PyTuple_CheckExact(row) || PyTuple_GET_SIZE(row) != (graph ? 3 : 2)) {
            Py_DECREF(row);
            PyErr_SetString(PyExc_TypeError, "invalid restore row");
            return NULL;
        }
        PyObject *target = PyTuple_GET_ITEM(row, 0), *saved = PyTuple_GET_ITEM(row, 1);
        if (graph) {
            int same = container_matches(target, saved, PyTuple_GET_ITEM(row, 2), deque_type);
            if (same < 0) { Py_DECREF(row); return NULL; }
            if (same) { Py_DECREF(row); continue; }
        } else if (skip_unchanged) {
            int same = input_matches_without_hooks(target, saved, deque_type);
            if (same < 0) { Py_DECREF(row); return NULL; }
            if (same) { Py_DECREF(row); continue; }
        }
        int restored = restore_container(target, saved, deque_type);
        Py_DECREF(row);
        if (restored < 0) return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *restore_inputs(PyObject *self, PyObject *args) { return restore_rows(args, 0); }
static PyObject *restore_graph(PyObject *self, PyObject *args) { return restore_rows(args, 1); }

static int namespace_state(PyObject *rows, int restore) {
    PyObject *name = PyUnicode_FromString("__dict__");
    if (name == NULL) return -1;
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(rows); i++) {
        PyObject *row = PyList_GET_ITEM(rows, i);
        if (!PyTuple_CheckExact(row) || PyTuple_GET_SIZE(row) != 2) {
            Py_DECREF(name); PyErr_SetString(PyExc_TypeError, "namespace pair required"); return -1;
        }
        PyObject *owner = PyTuple_GET_ITEM(row, 0), *old = PyTuple_GET_ITEM(row, 1);
        PyObject *current = PyObject_GetAttr(owner, name);
        if (current == NULL) { Py_DECREF(name); return -1; }
        int same = current == old;
        Py_DECREF(current);
        if (!same) {
            if (!restore) { Py_DECREF(name); return 0; }
            if (PyObject_GenericSetAttr(owner, name, old) < 0) { Py_DECREF(name); return -1; }
        }
    }
    Py_DECREF(name);
    return 1;
}

static int plain_member(PyObject *owner, PyObject *name) {
    if (Py_TYPE(owner)->tp_getattro != PyObject_GenericGetAttr) return 0;
    PyObject *mro = Py_TYPE(owner)->tp_mro;
    if (mro == NULL) return 0;
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(mro); i++) {
        PyTypeObject *base = (PyTypeObject *)PyTuple_GET_ITEM(mro, i);
        PyObject *descriptor = base->tp_dict == NULL ? NULL : PyDict_GetItemWithError(base->tp_dict, name);
        if (descriptor != NULL) return Py_TYPE(descriptor) == &PyMemberDescr_Type;
        if (PyErr_Occurred()) return -1;
    }
    return 0;
}

static int slot_state(PyObject *rows, int restore) {
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(rows); i++) {
        PyObject *row = PyList_GET_ITEM(rows, i);
        if (!PyTuple_CheckExact(row) || PyTuple_GET_SIZE(row) != 2 || !PyTuple_CheckExact(PyTuple_GET_ITEM(row, 1))) {
            PyErr_SetString(PyExc_TypeError, "slot state pair required"); return -1;
        }
        PyObject *owner = PyTuple_GET_ITEM(row, 0), *fields = PyTuple_GET_ITEM(row, 1);
        for (Py_ssize_t j = 0; j < PyTuple_GET_SIZE(fields); j++) {
            PyObject *field = PyTuple_GET_ITEM(fields, j);
            if (!PyTuple_CheckExact(field) || PyTuple_GET_SIZE(field) != 2) {
                PyErr_SetString(PyExc_TypeError, "slot field pair required"); return -1;
            }
            PyObject *name = PyTuple_GET_ITEM(field, 0), *old = PyTuple_GET_ITEM(field, 1);
            int safe = restore ? plain_member(owner, name) : 1;
            if (safe < 0) return -1;
            if (safe) {
                PyObject *current = PyObject_GetAttr(owner, name);
                int same = 0;
                if (current == NULL) {
                    if (!restore || !PyErr_ExceptionMatches(PyExc_AttributeError)) return -1;
                    PyErr_Clear();  /* A deleted plain slot must be restored. */
                } else {
                    same = current == old;
                    Py_DECREF(current);
                }
                if (!restore && !same) return 0;
                if (same) continue;
            }
            if (restore && PyObject_GenericSetAttr(owner, name, old) < 0) return -1;
        }
    }
    return 1;
}

static PyObject *restore_namespaces(PyObject *self, PyObject *args) {
    PyObject *rows;
    if (!PyArg_ParseTuple(args, "O!:restore_namespaces", &PyList_Type, &rows)) return NULL;
    if (namespace_state(rows, 1) < 0) return NULL;
    Py_RETURN_NONE;
}

static PyObject *restore_slots(PyObject *self, PyObject *args) {
    PyObject *rows;
    if (!PyArg_ParseTuple(args, "O!:restore_slots", &PyList_Type, &rows)) return NULL;
    if (slot_state(rows, 1) < 0) return NULL;
    Py_RETURN_NONE;
}

static PyObject *graph_matches(PyObject *self, PyObject *args) {
    PyObject *namespaces, *slots, *containers, *deque_type;
    if (!PyArg_ParseTuple(args, "O!O!O!O:graph_matches", &PyList_Type, &namespaces, &PyList_Type, &slots,
                         &PyList_Type, &containers, &deque_type)) return NULL;
    int same = namespace_state(namespaces, 0);
    if (same > 0) same = slot_state(slots, 0);
    if (same < 0) return NULL;
    if (!same) Py_RETURN_FALSE;
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(containers); i++) {
        PyObject *row = PyList_GET_ITEM(containers, i);
        if (!PyTuple_CheckExact(row) || PyTuple_GET_SIZE(row) != 3) {
            PyErr_SetString(PyExc_TypeError, "container state row required"); return NULL;
        }
        same = container_matches(PyTuple_GET_ITEM(row, 0), PyTuple_GET_ITEM(row, 1), PyTuple_GET_ITEM(row, 2), deque_type);
        if (same < 0) return NULL;
        if (!same) Py_RETURN_FALSE;
    }
    Py_RETURN_TRUE;
}

/* CPython invalidates a type's lookup version whenever its dictionary OR an
 * inherited dictionary changes. The artifact is built against this exact
 * CPython ABI with the GIL. A zero/unassigned tag always disables reuse. */
static PyObject *type_versions(PyObject *self, PyObject *args) {
    PyObject *types;
    if (!PyArg_ParseTuple(args, "O!:type_versions", &PyTuple_Type, &types)) return NULL;
    PyObject *result = PyTuple_New(PyTuple_GET_SIZE(types));
    if (result == NULL) return NULL;
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(types); i++) {
        PyObject *kind = PyTuple_GET_ITEM(types, i);
        if (!PyType_Check(kind)) {
            Py_DECREF(result); PyErr_SetString(PyExc_TypeError, "class required"); return NULL;
        }
        unsigned int version = ((PyTypeObject *)kind)->tp_version_tag;
        if (!version) { Py_DECREF(result); Py_RETURN_NONE; }
        PyObject *tag = PyLong_FromUnsignedLong(version);
        if (tag == NULL) { Py_DECREF(result); return NULL; }
        PyTuple_SET_ITEM(result, i, tag);
    }
    return result;
}

static PyObject *type_versions_match(PyObject *self, PyObject *args) {
    PyObject *types, *versions;
    if (!PyArg_ParseTuple(args, "O!O!:type_versions_match", &PyTuple_Type, &types,
                         &PyTuple_Type, &versions)) return NULL;
    if (PyTuple_GET_SIZE(types) != PyTuple_GET_SIZE(versions)) Py_RETURN_FALSE;
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(types); i++) {
        PyObject *kind = PyTuple_GET_ITEM(types, i);
        if (!PyType_Check(kind)) { PyErr_SetString(PyExc_TypeError, "class required"); return NULL; }
        unsigned long old = PyLong_AsUnsignedLong(PyTuple_GET_ITEM(versions, i));
        if (PyErr_Occurred()) return NULL;
        unsigned int current = ((PyTypeObject *)kind)->tp_version_tag;
        if (!current || old != current) Py_RETURN_FALSE;
    }
    Py_RETURN_TRUE;
}

static PyObject *function_codes_match(PyObject *self, PyObject *args) {
    PyObject *rows;
    if (!PyArg_ParseTuple(args, "O!:function_codes_match", &PyTuple_Type, &rows)) return NULL;
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(rows); i++) {
        PyObject *row = PyTuple_GET_ITEM(rows, i);
        if (!PyTuple_CheckExact(row) || PyTuple_GET_SIZE(row) != 2) {
            PyErr_SetString(PyExc_TypeError, "function/code pair required"); return NULL;
        }
        PyObject *function = PyTuple_GET_ITEM(row, 0);
        if (!PyFunction_Check(function) || (PyObject *)PyFunction_GET_CODE(function) != PyTuple_GET_ITEM(row, 1))
            Py_RETURN_FALSE;
    }
    Py_RETURN_TRUE;
}

static PyObject *position_stamp(PyObject *self, PyObject *args) {
    PyObject *rows;
    if (!PyArg_ParseTuple(args, "O!:position_stamp", &PyList_Type, &rows)) return NULL;
    PyObject *result = PyTuple_New(PyList_GET_SIZE(rows));
    PyObject *x_name = PyUnicode_FromString("x"), *y_name = PyUnicode_FromString("y");
    if (result == NULL || x_name == NULL || y_name == NULL) {
        Py_XDECREF(result); Py_XDECREF(x_name); Py_XDECREF(y_name); return NULL;
    }
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(rows); i++) {
        PyObject *row = PyList_GET_ITEM(rows, i);
        if (!PyTuple_CheckExact(row) || PyTuple_GET_SIZE(row) != 3) {
            Py_DECREF(result); Py_DECREF(x_name); Py_DECREF(y_name);
            PyErr_SetString(PyExc_TypeError, "position state triple required"); return NULL;
        }
        PyObject *owner = PyTuple_GET_ITEM(row, 0);
        PyObject *x = PyObject_GetAttr(owner, x_name), *y = PyObject_GetAttr(owner, y_name);
        if (x == NULL || y == NULL) {
            Py_XDECREF(x); Py_XDECREF(y); Py_DECREF(result); Py_DECREF(x_name); Py_DECREF(y_name); return NULL;
        }
        if (!PyLong_CheckExact(x) || !PyLong_CheckExact(y)) {
            Py_DECREF(x); Py_DECREF(y); Py_DECREF(result); Py_DECREF(x_name); Py_DECREF(y_name); Py_RETURN_NONE;
        }
        PyObject *pair = PyTuple_Pack(2, x, y);
        Py_DECREF(x); Py_DECREF(y);
        if (pair == NULL) { Py_DECREF(result); Py_DECREF(x_name); Py_DECREF(y_name); return NULL; }
        PyTuple_SET_ITEM(result, i, pair);
    }
    Py_DECREF(x_name); Py_DECREF(y_name);
    return result;
}

/* This direct-policy scanner retains no world/result state. Python certifies
 * class lookup/code identities once per class/pass; every live instance and
 * its current component lists are then inspected with the GIL held. */
static PyObject *empty_class_plan(PyObject *checker, PyObject *checked, PyObject *plans, PyObject *value, int unit) {
    PyObject *kind = (PyObject *)Py_TYPE(value);
    PyObject *plan = PyDict_GetItemWithError(checked, kind);
    if (plan != NULL) return Py_NewRef(plan);
    if (PyErr_Occurred()) return NULL;
    PyObject *record = Py_XNewRef(PyDict_GetItemWithError(plans, kind));
    int valid = record == Py_False;
    if (record != NULL && record != Py_False && PyTuple_CheckExact(record) && PyTuple_GET_SIZE(record) == 3) {
        PyObject *versions = PyTuple_GET_ITEM(record, 0), *codes = PyTuple_GET_ITEM(record, 1);
        if (PyTuple_CheckExact(versions) && PyTuple_GET_SIZE(versions) == 1 && PyTuple_CheckExact(codes)) {
            unsigned long old = PyLong_AsUnsignedLong(PyTuple_GET_ITEM(versions, 0));
            unsigned int current = ((PyTypeObject *)kind)->tp_version_tag;
            if (PyErr_Occurred()) { Py_DECREF(record); return NULL; }
            valid = current != 0 && old == current;
            for (Py_ssize_t i = 0; valid && i < PyTuple_GET_SIZE(codes); i++) {
                PyObject *row = PyTuple_GET_ITEM(codes, i);
                valid = PyTuple_CheckExact(row) && PyTuple_GET_SIZE(row) == 2
                    && PyFunction_Check(PyTuple_GET_ITEM(row, 0))
                    && (PyObject *)PyFunction_GET_CODE(PyTuple_GET_ITEM(row, 0)) == PyTuple_GET_ITEM(row, 1);
            }
        }
    }
    if (!valid) {
        Py_XDECREF(record);
        record = PyObject_CallFunctionObjArgs(checker, kind, unit ? Py_True : Py_False, NULL);
        if (record == NULL) return NULL;
        if (record != Py_False && (!PyTuple_CheckExact(record) || PyTuple_GET_SIZE(record) != 3)) {
            Py_DECREF(record); PyErr_SetString(PyExc_TypeError, "class proof record required"); return NULL;
        }
        if (PyDict_Size(plans) < 256 && PyDict_SetItem(plans, kind, record) < 0) { Py_DECREF(record); return NULL; }
    }
    plan = Py_NewRef(record == Py_False ? Py_False : PyTuple_GET_ITEM(record, 2));
    Py_DECREF(record);
    if (PyDict_SetItem(checked, kind, plan) < 0) { Py_DECREF(plan); return NULL; }
    return plan;
}

static int plain_flag(PyObject *value, PyObject *missing) {
    return value == NULL || value == missing || value == Py_None || PyBool_Check(value)
        || PyLong_CheckExact(value) || PyFloat_CheckExact(value)
        || PyUnicode_CheckExact(value) || PyBytes_CheckExact(value);
}

static int empty_component(PyObject *value, PyObject *checker, PyObject *checked, PyObject *plans,
                           PyObject *hook, PyObject *names, PyObject *missing,
                           PyObject *skill_type, int real_men) {
    PyObject *plan = empty_class_plan(checker, checked, plans, value, 0);
    if (plan == NULL) return -1;
    if (plan == Py_False) { Py_DECREF(plan); return 0; }
    if (!PyTuple_CheckExact(plan) || PyTuple_GET_SIZE(plan) != 2) {
        Py_DECREF(plan); PyErr_SetString(PyExc_TypeError, "class proof pair required"); return -1;
    }
    PyObject *data = PyObject_GenericGetDict(value, NULL);
    if (data == NULL) { Py_DECREF(plan); return -1; }
    int result = 0;
    if (!PyDict_CheckExact(data)) goto done;
    if (PyDict_GetItemWithError(data, PyTuple_GET_ITEM(names, 12)) != NULL
            || PyDict_GetItemWithError(data, PyTuple_GET_ITEM(names, 13)) != NULL
            || PyDict_GetItemWithError(data, hook) != NULL) goto done;
    PyObject *flag = PyDict_GetItemWithError(data, PyTuple_GET_ITEM(names, 14));
    if (flag == NULL) flag = PyTuple_GET_ITEM(plan, 0);
    if (!plain_flag(flag, missing)) goto done;
    if (real_men) {
        int skill = PyObject_IsInstance(value, skill_type);
        if (skill < 0) { result = -1; goto done; }
        if (skill) {
            PyObject *timing = PyDict_GetItemWithError(data, PyTuple_GET_ITEM(names, 15));
            if (timing == NULL) timing = PyTuple_GET_ITEM(plan, 1);
            if (!PyUnicode_CheckExact(timing)) goto done;
        }
    }
    result = 1;
done:
    Py_DECREF(data); Py_DECREF(plan);
    return PyErr_Occurred() ? -1 : result;
}

static PyObject *empty_component_pass(PyObject *self, PyObject *args) {
    PyObject *units, *battle, *checker, *hook, *skill_type, *names, *missing, *unit_plans, *component_plans;
    int real_men;
    if (!PyArg_ParseTuple(args, "O!OOOpOO!OO!O!:empty_component_pass", &PyDict_Type, &units,
                          &battle, &checker, &hook, &real_men, &skill_type,
                          &PyTuple_Type, &names, &missing, &PyDict_Type, &unit_plans, &PyDict_Type, &component_plans)) return NULL;
    if (PyTuple_GET_SIZE(names) != 16) {
        PyErr_SetString(PyExc_ValueError, "instance field names required"); return NULL;
    }
    PyObject *checked_units = PyDict_New(), *checked_components = PyDict_New();
    if (checked_units == NULL || checked_components == NULL) {
        Py_XDECREF(checked_units); Py_XDECREF(checked_components); return NULL;
    }
    int result = 1;
    Py_ssize_t cursor = 0;
    PyObject *unit;
    while (PyDict_Next(units, &cursor, NULL, &unit)) {
        PyObject *plan = empty_class_plan(checker, checked_units, unit_plans, unit, 1);
        if (plan == NULL) { result = -1; break; }
        int eligible = plan != Py_False;
        Py_DECREF(plan);
        if (!eligible) { result = 0; break; }
        PyObject *data = PyObject_GenericGetDict(unit, NULL);
        if (data == NULL) { result = -1; break; }
        if (!PyDict_CheckExact(data)) { Py_DECREF(data); result = 0; break; }
        for (int i = 0; i < 3; i++) {
            if (PyDict_GetItemWithError(data, PyTuple_GET_ITEM(names, i)) != NULL) { result = 0; break; }
        }
        PyObject *reference = PyDict_GetItemWithError(data, PyTuple_GET_ITEM(names, 3));
        if (reference != NULL && reference != Py_None
                && (!PyWeakref_CheckRefExact(reference) || PyWeakref_GET_OBJECT(reference) != battle)) result = 0;
        for (int i = 4; i < 8 && result > 0; i++) {
            PyObject *flag = PyDict_GetItemWithError(data, PyTuple_GET_ITEM(names, i));
            if (flag == NULL || !PyBool_Check(flag)) result = 0;
        }
        for (int i = 8; i < 11 && result > 0; i++) {
            PyObject *group = PyDict_GetItemWithError(data, PyTuple_GET_ITEM(names, i));
            if (group == NULL || !PyList_CheckExact(group)) { result = 0; break; }
            for (Py_ssize_t j = 0; j < PyList_GET_SIZE(group) && result > 0; j++) {
                result = empty_component(PyList_GET_ITEM(group, j), checker, checked_components, component_plans,
                                         hook, names, missing, skill_type, real_men);
            }
        }
        PyObject *definitions = PyDict_GetItemWithError(data, PyTuple_GET_ITEM(names, 11));
        if (result > 0 && definitions != NULL) {
            if (!PyDict_CheckExact(definitions)) result = 0;
            else {
                Py_ssize_t pos = 0;
                PyObject *component;
                while (result > 0 && PyDict_Next(definitions, &pos, NULL, &component)) {
                    result = empty_component(component, checker, checked_components, component_plans,
                                             hook, names, missing, skill_type, real_men);
                }
            }
        }
        Py_DECREF(data);
        if (result <= 0) break;
    }
    Py_DECREF(checked_units); Py_DECREF(checked_components);
    if (result < 0 || PyErr_Occurred()) return NULL;
    return PyBool_FromLong(result);
}

static PyObject *component_snapshot(PyObject *self, PyObject *args) {
    PyObject *units, *battle, *unit, *checker, *hook, *skill_type, *names, *missing, *unit_plans, *component_plans;
    int arena;
    if (!PyArg_ParseTuple(args, "O!OOOOpOO!OO!O!:component_snapshot", &PyDict_Type, &units,
                          &battle, &unit, &checker, &hook, &arena, &skill_type,
                          &PyTuple_Type, &names, &missing, &PyDict_Type, &unit_plans, &PyDict_Type, &component_plans)) return NULL;
    if (PyTuple_GET_SIZE(names) != 16) { PyErr_SetString(PyExc_ValueError, "instance names required"); return NULL; }
    PyObject *checked_units = PyDict_New(), *checked = PyDict_New(), *data = NULL, *result = NULL, *seen = NULL;
    int eligible = 0;
    if (checked_units == NULL || checked == NULL) goto done;
    PyObject *plan = empty_class_plan(checker, checked_units, unit_plans, unit, 1);
    if (plan == NULL) goto done;
    eligible = plan != Py_False;
    Py_DECREF(plan);
    if (!eligible) goto unsupported;
    data = PyObject_GenericGetDict(unit, NULL);
    if (data == NULL) goto done;
    if (!PyDict_CheckExact(data)) goto unsupported;
    for (int i = 0; i < 3; i++) if (PyDict_GetItemWithError(data, PyTuple_GET_ITEM(names, i)) != NULL) goto unsupported;
    PyObject *reference = PyDict_GetItemWithError(data, PyTuple_GET_ITEM(names, 3));
    if (reference == NULL || !PyWeakref_CheckRefExact(reference) || PyWeakref_GET_OBJECT(reference) != battle) goto unsupported;
    PyObject *flags[4];
    for (int i = 0; i < 4; i++) {
        flags[i] = PyDict_GetItemWithError(data, PyTuple_GET_ITEM(names, i + 4));
        if (flags[i] == NULL || !PyBool_Check(flags[i])) goto unsupported;
    }
    if (arena && flags[0] == Py_True) { result = PyList_New(0); goto done; }
    PyObject *groups[3];
    for (int i = 0; i < 3; i++) {
        groups[i] = PyDict_GetItemWithError(data, PyTuple_GET_ITEM(names, i + 8));
        if (groups[i] == NULL || !PyList_CheckExact(groups[i])) goto unsupported;
        for (Py_ssize_t j = 0; j < PyList_GET_SIZE(groups[i]); j++) {
            int safe = empty_component(PyList_GET_ITEM(groups[i], j), checker, checked, component_plans,
                                       hook, names, missing, skill_type, 0);
            if (safe < 0) goto done;
            if (!safe) goto unsupported;
        }
    }
    PyObject *definitions = PyDict_GetItemWithError(data, PyTuple_GET_ITEM(names, 11));
    if (definitions != NULL) {
        if (!PyDict_CheckExact(definitions)) goto unsupported;
        Py_ssize_t pos = 0;
        PyObject *value;
        while (PyDict_Next(definitions, &pos, NULL, &value)) {
            int safe = empty_component(value, checker, checked, component_plans, hook, names, missing, skill_type, 0);
            if (safe < 0) goto done;
            if (!safe) goto unsupported;
        }
    }
    /* Live summons could receive traits from ANY source. Prove the entire
     * current world has empty projection/discovery, or use the Python path. */
    if ((flags[2] == Py_True || flags[3] == Py_True) && flags[0] == Py_False && flags[1] == Py_True) {
        PyObject *scan_args = PyTuple_Pack(10, units, battle, checker, hook, Py_False,
                                          skill_type, names, missing, unit_plans, component_plans);
        if (scan_args == NULL) goto done;
        PyObject *safe = empty_component_pass(self, scan_args);
        Py_DECREF(scan_args);
        if (safe == NULL) goto done;
        eligible = safe == Py_True;
        Py_DECREF(safe);
        if (!eligible) goto unsupported;
    }
    int suppressed = 0;
    for (Py_ssize_t i = 0; !suppressed && i < PyList_GET_SIZE(groups[2]); i++) {
        PyObject *status = PyList_GET_ITEM(groups[2], i);
        PyObject *state = PyObject_GenericGetDict(status, NULL);
        if (state == NULL) goto done;
        PyObject *flag = PyDict_GetItemWithError(state, PyTuple_GET_ITEM(names, 14));
        if (flag == NULL) {
            PyObject *row = PyDict_GetItemWithError(checked, (PyObject *)Py_TYPE(status));
            if (row == NULL) { Py_DECREF(state); goto done; }
            flag = PyTuple_GET_ITEM(row, 0);
        }
        suppressed = flag == missing ? 0 : PyObject_IsTrue(flag);
        Py_DECREF(state);
        if (suppressed < 0) goto done;
    }
    result = PyList_New(0); seen = PySet_New(NULL);
    if (result == NULL || seen == NULL) goto done;
    for (int i = 0; i < 3; i++) {
        if (i == 1 && suppressed) continue;
        for (Py_ssize_t j = 0; j < PyList_GET_SIZE(groups[i]); j++) {
            PyObject *value = PyList_GET_ITEM(groups[i], j);
            if (PyList_Append(result, value) < 0) goto done;
            if (i == 0) {
                PyObject *identity = PyLong_FromVoidPtr(value);
                if (identity == NULL) goto done;
                int added = PySet_Add(seen, identity);
                Py_DECREF(identity);
                if (added < 0) goto done;
            }
        }
    }
    if (definitions != NULL) {
        Py_ssize_t pos = 0;
        PyObject *value;
        while (PyDict_Next(definitions, &pos, NULL, &value)) {
            PyObject *identity = PyLong_FromVoidPtr(value);
            if (identity == NULL) goto done;
            int contains = PySet_Contains(seen, identity);
            Py_DECREF(identity);
            if (contains < 0 || (!contains && PyList_Append(result, value) < 0)) goto done;
        }
    }
    goto done;
unsupported:
    if (!PyErr_Occurred()) { Py_XDECREF(result); result = Py_NewRef(Py_None); }
done:
    Py_XDECREF(checked_units); Py_XDECREF(checked); Py_XDECREF(data); Py_XDECREF(seen);
    if (PyErr_Occurred()) { Py_XDECREF(result); return NULL; }
    return result;
}

static PyMethodDef methods[] = {
    {"component_snapshot", component_snapshot, METH_VARARGS, "Build only certified static eager component snapshots, otherwise return None."},
    {"empty_component_pass", empty_component_pass, METH_VARARGS, "Inspect current no-op notification members without caching battle state."},
    {"capture_inputs", capture_inputs, METH_VARARGS, "Save exact closure/input containers and count visited references."},
    {"restore_inputs", restore_inputs, METH_VARARGS, "Restore input containers in reverse capture order."},
    {"containers_match", containers_match, METH_VARARGS, "Validate every saved container by ordered reference identity."},
    {"restore_graph", restore_graph, METH_VARARGS, "Restore changed graph containers in reverse capture order."},
    {"graph_matches", graph_matches, METH_VARARGS, "Validate all namespace, slot and container identities in order."},
    {"restore_namespaces", restore_namespaces, METH_VARARGS, "Restore original namespace identities."},
    {"restore_slots", restore_slots, METH_VARARGS, "Restore slots, skipping unchanged plain C member writes only."},
    {"type_versions", type_versions, METH_VARARGS, "Capture complete CPython type lookup invalidation tags."},
    {"type_versions_match", type_versions_match, METH_VARARGS, "Reject changed, inherited or unassigned type tags."},
    {"function_codes_match", function_codes_match, METH_VARARGS, "Check original function code identities without Python callbacks."},
    {"position_stamp", position_stamp, METH_VARARGS, "Observe every exact integer frozen coordinate in order."},
    {NULL, NULL, 0, NULL}
};
static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_wujiang_prediction_native", NULL, -1, methods,
    NULL, NULL, NULL, NULL
};
PyMODINIT_FUNC PyInit__wujiang_prediction_native(void) {
    PyObject *result = PyModule_Create(&module);
    if (result == NULL) return NULL;
    if (PyModule_AddStringConstant(result, "source_id", WUJIANG_PREDICTION_SOURCE_ID) < 0) {
        Py_DECREF(result);
        return NULL;
    }
    return result;
}
