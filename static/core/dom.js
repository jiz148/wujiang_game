// Leaf-level DOM helpers.
//
// This module imports nothing on purpose. Almost every other module needs `$`,
// so hanging it off net.js or ui.js would drag those into import cycles that
// break on a `const` binding's temporal dead zone.
export const $ = (id) => document.getElementById(id);
