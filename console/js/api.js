/**
 * HTTP client for the RoboGuide console.
 *
 * Live requests go through the same-origin proxy exposed by `console/serve.py`
 * (`/proxy/controller/...`, `/proxy/mission/...`) because the RoboGuide HTTP
 * APIs are same-origin-only. The client is read-only except for the existing
 * Mission submit/cancel endpoints that external callers already use.
 */

export class Api {
  /**
   * @param {string} controllerBase - Proxied Controller base path.
   * @param {string} missionBase - Proxied Mission Service base path.
   */
  constructor(controllerBase = "/proxy/controller", missionBase = "/proxy/mission") {
    this.controllerBase = controllerBase;
    this.missionBase = missionBase;
  }

  /**
   * Performs one JSON request against an upstream.
   * @param {string} base - Base path.
   * @param {string} path - Suffix path (no leading slash).
   * @param {?object} body - JSON body for POST, null for GET.
   * @returns {Promise<object>} Parsed JSON response.
   */
  async #request(base, path, body = null) {
    const response = await fetch(`${base}/${path}`, {
      method: body === null ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: body === null ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    let json = {};
    try { json = text ? JSON.parse(text) : {}; } catch { json = { error: text }; }
    if (!response.ok) {
      throw new Error(json.error ?? `${response.status} ${response.statusText}`);
    }
    return json;
  }

  /** @returns {Promise<object>} Controller health document. */
  health() { return this.#request(this.controllerBase, "healthz"); }

  /** @returns {Promise<object>} Node inventory projection. */
  inventory() { return this.#request(this.controllerBase, "v1/inventory"); }

  /**
   * Fetches one page of the evidence event log.
   * @param {?number} after - Exclusive sequence cursor; null starts at the log head.
   * @param {number} limit - Page size.
   * @returns {Promise<object[]>} Raw event records in sequence order.
   */
  async events(after, limit = 200) {
    const cursor = after === null ? "" : `after=${after}&`;
    const doc = await this.#request(this.controllerBase, `v1/events?${cursor}limit=${limit}`);
    return doc.events ?? [];
  }

  /**
   * Fetches one mission projection.
   * @param {string} missionId - Mission identity.
   * @returns {Promise<object>} Mission execution projection.
   */
  mission(missionId) { return this.#request(this.controllerBase, `v1/missions/${encodeURIComponent(missionId)}`); }

  /**
   * Submits a complete versioned MissionPlan.
   * @param {object} plan - MissionPlan document.
   * @returns {Promise<object>} Acceptance envelope `{mission_id, group_id, status}`.
   */
  submit(plan) { return this.#request(this.controllerBase, "v1/missions", plan); }

  /**
   * Requests durable Mission cancellation.
   * @param {string} missionId - Mission identity.
   * @returns {Promise<object>} Cancellation status envelope.
   */
  cancel(missionId) { return this.#request(this.controllerBase, `v1/missions/${encodeURIComponent(missionId)}/cancel`, {}); }

  /** @returns {Promise<object>} Scheduling calendar reservations. */
  schedulingReservations() { return this.#request(this.controllerBase, "v1/scheduling-reservations"); }

  /** @returns {Promise<object>} Read-only federated State records view. */
  stateRecords() { return this.#request(this.controllerBase, "v1/state/records"); }

  /**
   * Creates a text Mission Request via Mission Intelligence.
   * @param {string} instruction - Free-form instruction text.
   * @returns {Promise<object>} Durable Mission Request record.
   */
  createMissionRequest(instruction) {
    return this.#request(this.missionBase, "v1/mission-requests", { instruction });
  }

  /**
   * Fetches one durable Mission Request projection.
   * @param {string} requestId - Mission Request identity.
   * @returns {Promise<object>} Mission Request record.
   */
  missionRequest(requestId) { return this.#request(this.missionBase, `v1/mission-requests/${encodeURIComponent(requestId)}`); }

  /**
   * Loads one static scenario plan shipped with the console.
   * @param {string} name - Scenario file name under `console/scenarios/`.
   * @returns {Promise<object>} MissionPlan document.
   */
  async scenarioPlan(name) {
    const response = await fetch(`scenarios/${name}`);
    if (!response.ok) throw new Error(`scenario ${name} unavailable`);
    return response.json();
  }
}
