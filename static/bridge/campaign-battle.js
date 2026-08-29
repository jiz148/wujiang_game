// The seam between the campaign and the battlefield.
//
// Campaign screens reach the battle layer only through this file, and the
// battle layer reaches campaign state only through it too. It mirrors the
// backend's wujiang.bridge package: if you are adding a call that crosses
// between the two domains, it belongs here and nowhere else.

export { syncStrategyCampaignFromRoomPayload } from '../strategic/api.js';
export { loadRecordedMatchEnds } from '../strategic/workbench.js';
export { applyRoomPayload, canReclaimSeatByName, storedIdentityForCurrentRoom } from '../tactical/room-api.js';
export { loadStoredIdentity, saveStoredIdentity, syncSelectedUnitAfterStateChange } from '../tactical/session.js';
export { actionLabel, actionTitle } from '../tactical/targeting.js';
