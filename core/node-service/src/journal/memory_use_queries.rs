//! Independent observation journals; original command clocks and receipts remain immutable.

use super::*;
use domain::{MemoryUseQuery, MemoryUseQueryReceipt};
use serde::{Deserialize, Serialize};

/// Observation-only receiver deadline, separate from preparation and use authority budgets.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryUseObservationBudget {
    /// Actual original receiver boot.
    pub boot_id: String,
    /// Actual original Linux time namespace.
    pub time_namespace: String,
    /// Original bounded receive lower time.
    pub received_lower_ms: u64,
    /// Exclusive original observation deadline.
    pub deadline_lower_ms: u64,
}

impl MemoryUseObservationBudget {
    /// Freezes at most five minutes of observation; it grants no content permission.
    fn admit(now: &ReceiverClockObservation, width: u64) -> Result<Self, JournalError> {
        if !(1..=300_000).contains(&width)
            || width <= now.uncertainty_ms
            || now.boot_id.is_empty()
            || now.time_namespace.is_empty()
            || !(1..=1000).contains(&now.uncertainty_ms)
        {
            return Err(JournalError::InvalidSpec(
                "readonly observation budget is unavailable".into(),
            ));
        }
        Ok(Self {
            boot_id: now.boot_id.clone(),
            time_namespace: now.time_namespace.clone(),
            received_lower_ms: now.lower_ms,
            deadline_lower_ms: now
                .lower_ms
                .checked_add(width)
                .ok_or_else(|| JournalError::InvalidSpec("observation deadline overflow".into()))?,
        })
    }

    /// Rejects reboot, namespace change, rollback and any widening of the original bound.
    pub fn remaining_ms(&self, now: &ReceiverClockObservation) -> u64 {
        if self.boot_id != now.boot_id
            || self.time_namespace != now.time_namespace
            || now.lower_ms < self.received_lower_ms
            || !(1..=1000).contains(&now.uncertainty_ms)
            || !(1..=300_000).contains(
                &self
                    .deadline_lower_ms
                    .saturating_sub(self.received_lower_ms),
            )
        {
            return 0;
        }
        self.deadline_lower_ms
            .saturating_sub(now.lower_ms.saturating_add(now.uncertainty_ms))
    }
}

/// Exact once-admitted observation and its immutable first completion.
#[derive(Debug, Clone)]
pub struct JournalMemoryUseQuery {
    /// Complete original query bytes.
    pub query: MemoryUseQuery,
    /// Original query receiver budget, bounded by the original session.
    pub budget: MemoryUseObservationBudget,
    /// First durable result; replay never re-runs queried providers.
    pub receipt: Option<MemoryUseQueryReceipt>,
}

impl ExecutionJournal {
    /// Looks up original provider effects without acquiring or refreshing authority.
    pub fn memory_use_effect(
        &self,
        command: &str,
        provider: &str,
    ) -> Result<Option<memory_use::JournalMemoryUseEffect>, JournalError> {
        {
            let connection = self.lock_connection()?;
            memory_use::read_effect(&connection, command, provider)
        }
    }

    /// Reserves one monotonic query and its original session clock before any GET.
    pub fn admit_memory_use_observation(
        &self,
        query: &MemoryUseQuery,
        now: &ReceiverClockObservation,
        remaining: u64,
        session_remaining: u64,
    ) -> Result<JournalMemoryUseQuery, JournalError> {
        query.validate().map_err(JournalError::InvalidSpec)?;
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some(old) = read_query(&tx, &query.query_id)? {
            if old.query != *query {
                return Err(JournalError::InvalidSpec(
                    "original readonly use query changed".into(),
                ));
            }
            return Ok(old);
        }
        let original = memory_use::read_command(&tx, &query.command_id)?.ok_or_else(|| {
            JournalError::InvalidSpec("readonly query has no original revoke".into())
        })?;
        let context = memory_preparation::read_preparation(&tx, &original.command.preparation_id)?
            .ok_or_else(|| {
                JournalError::InvalidSpec("readonly query lost original preparation".into())
            })?;
        query
            .validate_original(&original.command, &context.preparation)
            .map_err(JournalError::InvalidSpec)?;
        if remaining == 0
            || remaining > query.valid_for_ms
            || session_remaining == 0
            || session_remaining > query.session_budget_ms
            || remaining > session_remaining
        {
            return Err(JournalError::InvalidSpec(
                "query transport renewed its original observation budget".into(),
            ));
        }
        let session: Option<(u64,u64,u64,String)> = tx.query_row("SELECT opened_at_ms,width_ms,generation,budget_json FROM memory_use_observation_sessions WHERE command_id=?1", [&query.command_id], |row| Ok((row.get(0)?,row.get(1)?,row.get(2)?,row.get(3)?))).optional()?;
        let total: u64 = tx.query_row(
            "SELECT COUNT(*) FROM memory_use_observation_queries",
            [],
            |row| row.get(0),
        )?;
        if total >= 1024 {
            return Err(JournalError::InvalidSpec(
                "readonly use query journal is full".into(),
            ));
        }
        let session_budget = if let Some((opened, width, generation, budget)) = session {
            let budget: MemoryUseObservationBudget = serde_json::from_str(&budget)?;
            if opened != query.opened_at_ms
                || width != query.session_budget_ms
                || generation >= query.generation
                || budget.remaining_ms(now) == 0
            {
                return Err(JournalError::InvalidSpec(
                    "original observation session is stale, expired or changed".into(),
                ));
            }
            budget
        } else {
            let count: u64 = tx.query_row(
                "SELECT COUNT(*) FROM memory_use_observation_queries",
                [],
                |row| row.get(0),
            )?;
            if count >= 1024 {
                return Err(JournalError::InvalidSpec(
                    "readonly use query journal is full".into(),
                ));
            }
            MemoryUseObservationBudget::admit(now, session_remaining)?
        };
        let budget = MemoryUseObservationBudget::admit(
            now,
            remaining.min(session_budget.remaining_ms(now)),
        )?;
        tx.execute("INSERT INTO memory_use_observation_sessions VALUES (?1,?2,?3,?4,?5) ON CONFLICT(command_id) DO UPDATE SET generation=excluded.generation",params![query.command_id,query.opened_at_ms,query.session_budget_ms,query.generation,serde_json::to_string(&session_budget)?])?;
        tx.execute("INSERT INTO memory_use_observation_queries(query_id,command_id,query_json,budget_json) VALUES (?1,?2,?3,?4)",params![query.query_id,query.command_id,serde_json::to_string(query)?,serde_json::to_string(&budget)?])?;
        tx.commit()?;
        Ok(JournalMemoryUseQuery {
            query: query.clone(),
            budget,
            receipt: None,
        })
    }

    /// Reads exact original observation state without scheduling effects.
    pub fn memory_use_observation(
        &self,
        id: &str,
    ) -> Result<Option<JournalMemoryUseQuery>, JournalError> {
        {
            let connection = self.lock_connection()?;
            read_query(&connection, id)
        }
    }

    /// Commits a once-only GET intent; interruption consumes that provider's opportunity.
    pub fn begin_memory_use_observation_provider(
        &self,
        query: &MemoryUseQuery,
        provider: &str,
    ) -> Result<bool, JournalError> {
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let original = read_query(&tx, &query.query_id)?
            .ok_or_else(|| JournalError::InvalidSpec("query was never admitted".into()))?;
        if original.query != *query
            || !query.provider_ids.iter().any(|p| p == provider)
            || original
                .budget
                .remaining_ms(&ReceiverClockObservation::read()?)
                == 0
        {
            return Err(JournalError::InvalidSpec(
                "original readonly query is changed or expired".into(),
            ));
        }
        let changed = tx.execute(
            "INSERT OR IGNORE INTO memory_use_observation_providers VALUES (?1,?2)",
            params![query.query_id, provider],
        )?;
        tx.commit()?;
        Ok(changed == 1)
    }

    /// Appends a first immutable observation result; original revoke receipt and budgets are untouched.
    pub fn record_memory_use_observation(
        &self,
        receipt: &MemoryUseQueryReceipt,
    ) -> Result<MemoryUseQueryReceipt, JournalError> {
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let original = read_query(&tx, &receipt.query_id)?
            .ok_or_else(|| JournalError::InvalidSpec("observation has no original query".into()))?;
        let command = memory_use::read_command(&tx, &original.query.command_id)?
            .ok_or_else(|| JournalError::Corrupt("original revoke is absent".into()))?;
        let context =
            memory_preparation::read_preparation(&tx, &command.command.preparation_id)?
                .ok_or_else(|| JournalError::Corrupt("original preparation is absent".into()))?;
        receipt
            .validate(&original.query, &command.command, &context.preparation)
            .map_err(JournalError::InvalidSpec)?;
        if let Some(old) = original.receipt {
            if old != *receipt {
                return Err(JournalError::InvalidSpec(
                    "readonly observation result changed".into(),
                ));
            }
            return Ok(old);
        }
        for observation in &receipt.observations {
            if let Some(proof) = &observation.proof {
                let effect =
                    memory_use::read_effect(&tx, &command.command.command_id, &proof.provider_id)?
                        .ok_or_else(|| {
                            JournalError::Corrupt("proof has no original provider effect".into())
                        })?;
                if effect.receipt.as_ref() != Some(&proof.original_receipt) {
                    return Err(JournalError::Corrupt(
                        "observation proof differs from retained effect".into(),
                    ));
                }
            }
        }
        tx.execute(
            "UPDATE memory_use_observation_queries SET receipt_json=?1 WHERE query_id=?2",
            params![serde_json::to_string(receipt)?, receipt.query_id],
        )?;
        tx.commit()?;
        Ok(receipt.clone())
    }
}

/// Reads the original query, clock and receipt without deriving missing history.
fn read_query(
    connection: &Connection,
    id: &str,
) -> Result<Option<JournalMemoryUseQuery>, JournalError> {
    let row:Option<(String,String,Option<String>)>=connection.query_row("SELECT query_json,budget_json,receipt_json FROM memory_use_observation_queries WHERE query_id=?1",[id],|row|Ok((row.get(0)?,row.get(1)?,row.get(2)?))).optional()?;
    row.map(|(query, budget, receipt)| {
        let retained = JournalMemoryUseQuery {
            query: serde_json::from_str(&query)?,
            budget: serde_json::from_str(&budget)?,
            receipt: receipt.map(|s| serde_json::from_str(&s)).transpose()?,
        };
        retained.query.validate().map_err(JournalError::Corrupt)?;
        let command = memory_use::read_command(connection, &retained.query.command_id)?
            .ok_or_else(|| {
                JournalError::Corrupt("readonly query lost its original command".into())
            })?;
        let context =
            memory_preparation::read_preparation(connection, &command.command.preparation_id)?
                .ok_or_else(|| {
                    JournalError::Corrupt("readonly query lost its original context".into())
                })?;
        retained
            .query
            .validate_original(&command.command, &context.preparation)
            .map_err(JournalError::Corrupt)?;
        if retained.query.query_id != id
            || retained.budget.boot_id.is_empty()
            || retained.budget.time_namespace.is_empty()
            || !(1..=retained.query.valid_for_ms).contains(
                &retained
                    .budget
                    .deadline_lower_ms
                    .saturating_sub(retained.budget.received_lower_ms),
            )
        {
            return Err(JournalError::Corrupt(
                "readonly query changed its original identity or receive budget".into(),
            ));
        }
        if let Some(receipt) = &retained.receipt {
            receipt
                .validate(&retained.query, &command.command, &context.preparation)
                .map_err(JournalError::Corrupt)?;
            for observation in &receipt.observations {
                if let Some(proof) = &observation.proof {
                    let effect = memory_use::read_effect(
                        connection,
                        &command.command.command_id,
                        &proof.provider_id,
                    )?
                    .ok_or_else(|| {
                        JournalError::Corrupt("readonly proof has no original effect".into())
                    })?;
                    if effect.receipt.as_ref() != Some(&proof.original_receipt) {
                        return Err(JournalError::Corrupt(
                            "readonly proof changed its original retained result".into(),
                        ));
                    }
                }
            }
        }
        Ok(retained)
    })
    .transpose()
}
