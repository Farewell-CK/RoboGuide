//! Read-only discovery of already authorized original generation tickets.

use super::*;

/// Decodes one bounded UTF-8 query value without interpreting it as an authority selector.
fn decode(value: &str) -> Result<String, String> {
    if value.len() > 384 {
        return Err("runtime work cursor exceeds its bound".into());
    }
    let mut bytes = value.bytes();
    let mut decoded = Vec::new();
    while let Some(byte) = bytes.next() {
        decoded.push(match byte {
            b'%' => {
                let high = bytes.next().ok_or("incomplete work cursor escape")?;
                let low = bytes.next().ok_or("incomplete work cursor escape")?;
                let pair = [high, low];
                u8::from_str_radix(
                    std::str::from_utf8(&pair).map_err(|_| "invalid work cursor escape")?,
                    16,
                )
                .map_err(|_| "invalid work cursor escape")?
            }
            b'+' => b' ',
            other => other,
        });
    }
    let decoded = String::from_utf8(decoded).map_err(|_| "work cursor is not UTF-8")?;
    if !identity(&decoded) {
        return Err("invalid bounded work cursor".into());
    }
    Ok(decoded)
}

/// Refuses duplicate, unknown and unbounded discovery parameters before reading authority.
pub(crate) fn work_query(query: &str) -> Result<(Option<String>, usize), String> {
    let mut after = None;
    let mut limit = None;
    for pair in query.split('&').filter(|pair| !pair.is_empty()) {
        let (key, value) = pair.split_once('=').ok_or("invalid runtime work query")?;
        match key {
            "after" if after.is_none() => after = Some(decode(value)?),
            "limit" if limit.is_none() => {
                let value: usize = value.parse().map_err(|_| "invalid runtime work limit")?;
                if !(1..=32).contains(&value) {
                    return Err("runtime work limit must be 1-32".into());
                }
                limit = Some(value);
            }
            _ => return Err("unknown or repeated runtime work parameter".into()),
        }
    }
    Ok((after, limit.unwrap_or(32)))
}

/// Projects current tickets only; actual caller, source and budgets remain mandatory at consumption.
pub(crate) fn work_page(
    controller: &ControllerState,
    principal: &str,
    tenant: &str,
    after: Option<&str>,
    limit: usize,
    now: domain::TimestampMs,
) -> Result<serde_json::Value, String> {
    if !identity(principal) || !identity(tenant) || tenant == "*" || !(1..=32).contains(&limit) {
        return Err("runtime work requires an exact authenticated tenant and bounded page".into());
    }
    let kernel = KernelTime::observe()?;
    let mut items = Vec::new();
    for (mission, state) in &controller.runtime_revisions {
        if after.is_some_and(|after| mission.as_str() <= after)
            || state.tenant != tenant
            || state.policy.proposer != principal
            || !generation::active(controller, state, now, &kernel)
        {
            continue;
        }
        let id = domain::MissionId::new(mission).map_err(|error| error.to_string())?;
        let Some(execution) = controller.orchestrator.execution(&id) else {
            continue;
        };
        let Some(switch) = state.switches.iter().find(|switch| {
            switch.generation
                && switch.phase == Phase::Generating
                && &switch.source_group == execution.group_id()
                && switch.budget.remaining(&kernel) > 0
        }) else {
            continue;
        };
        let Some(candidate) = state.candidates.get(&switch.ticket_id) else {
            continue;
        };
        let remaining = state
            .budget
            .remaining(&kernel)
            .min(switch.budget.remaining(&kernel))
            .min(candidate.budget.remaining(&kernel));
        if remaining == 0
            || candidate.principal != principal
            || candidate.base_revision != execution.plan_revision()
            || candidate.base_digest != orchestration::mission_plan_digest(execution.plan())
            || candidate.rejected.is_some()
        {
            continue;
        }
        items.push(serde_json::json!({
            "mission_id":mission,"ticket_id":switch.ticket_id,
            "source_revision":candidate.base_revision,"source_plan_digest":candidate.base_digest,
            "remaining_ms":remaining
        }));
        if items.len() > limit {
            break;
        }
    }
    let more = items.len() > limit;
    items.truncate(limit);
    let next = more.then(|| items.last().expect("nonempty bounded page")["mission_id"].clone());
    Ok(serde_json::json!({
        "schema_version":"roboguide.runtime-plan-work/v0.1",
        "tenant":tenant,"principal":principal,"items":items,"next_after":next
    }))
}
