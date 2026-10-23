//! Actual original child read stream with bounded buffers and independent complete-byte validation.

use super::*;
use crate::controller_ingress::content_read::{ReadHead, ReadPurpose};
use std::io;

/// Original live readonly connection; dropping it cancels reception without inventing source closure.
pub struct ControllerContentRead {
    /// Actual original configured client and source writer leases remain retained during the read.
    client: ControllerContentClient,
    /// Actual original mutual-TLS connection and its first nonrenewing clock.
    exchange: Exchange,
    /// Exact original opened qualified source and process/target evidence.
    head: ReadHead,
    /// At most one 64 KiB actual network chunk; never allocated from an unbounded peer size.
    pending: Vec<u8>,
    /// Next unread byte within the original pending chunk.
    offset: usize,
    /// Actual returned sequence, independently of the source's own hash.
    hasher: Sha256,
    /// Actual count returned through this original child handle.
    returned: u64,
    /// Actual full terminal receipt was validated; closing a socket alone never sets this.
    complete: bool,
    /// Actual validated original close receipt, absent until the complete original stream closes.
    receipt: Option<crate::content_store::ReplicaContentReaderReceipt>,
    /// Any prior read error irreversibly fences this original stream.
    failed: bool,
}

impl ControllerContentClient {
    /// Opens only the exact committed original recovery, independent of public content availability.
    pub fn open_upload_recovery(
        &self,
        record: &domain::ArtifactUploadRecovery,
    ) -> Result<ControllerContentRead, String> {
        self.check_upload_recovery(record)?;
        let reader = self.open_content_read_named(
            ReadPurpose::PreparedRecovery {
                command_id: record.command_id.clone(),
            },
            &domain::artifact_adoption_digest(record)?[7..],
            Some(&record.reader_id),
        )?;
        if reader.content() != &crate::controller_ingress::recovery::source_key(record)? {
            return Err("recovery read changed its exact original source key".into());
        }
        Ok(reader)
    }

    /// Opens one current published qualified source; the composition still enforces real ACL/use per chunk.
    pub fn open_published_content(
        &self,
        digest: &str,
        context_digest: &str,
    ) -> Result<ControllerContentRead, String> {
        let digest = artifact_store::normalize_digest(digest).map_err(|error| error.to_string())?;
        self.open_content_read(ReadPurpose::Published { digest }, context_digest)
    }

    /// Verifies actual complete retained bytes for internal integrity, without publishing CAS or granting use.
    pub fn verify_retained_content(&self, content: &ArtifactContentKey) -> Result<(), String> {
        let context = format!(
            "{:x}",
            Sha256::digest(serde_json::to_vec(content).map_err(|error| error.to_string())?)
        );
        let mut reader = self.open_content_read(
            ReadPurpose::RetainedIntegrity {
                content: content.clone(),
            },
            &context,
        )?;
        io::copy(&mut reader, &mut io::sink()).map_err(|error| error.to_string())?;
        if !reader.complete {
            return Err("original retained content integrity read is incomplete".into());
        }
        Ok(())
    }

    /// Captures an actual new reader ID before I/O and validates the parent's original process/target reply.
    fn open_content_read(
        &self,
        purpose: ReadPurpose,
        context_digest: &str,
    ) -> Result<ControllerContentRead, String> {
        self.open_content_read_named(purpose, context_digest, None)
    }

    /// Opens only the original recovery reader ID, or admits a fresh ordinary integrity/public reader.
    fn open_content_read_named(
        &self,
        purpose: ReadPurpose,
        context_digest: &str,
        original_reader: Option<&str>,
    ) -> Result<ControllerContentRead, String> {
        if !crate::content::hex_digest(context_digest) {
            return Err("invalid original read context digest".into());
        }
        let reader_id = match original_reader {
            Some(id) => id.to_string(),
            None => format!("child-read:{}", crate::supervision::new_process_instance()?),
        };
        let mut exchange = self.connect(ChildCommand::ReadContentV1 {
            reader_id: reader_id.clone(),
            purpose: purpose.clone(),
            context_digest: context_digest.into(),
        })?;
        let head = match self.read::<ChildReply>(&mut exchange)? {
            ChildReply::ReadReady(head) => *head,
            ChildReply::Rejected(error) => return Err(error),
            _ => return Err("original qualified reader admission reply differs".into()),
        };
        head.target.validate()?;
        crate::supervision::validate_process(&head.process)?;
        head.content.validate()?;
        let expected = match &purpose {
            ReadPurpose::Published { digest } => head.content.content_digest == *digest,
            ReadPurpose::PreparedRecovery { .. } => true,
            ReadPurpose::RetainedIntegrity { content } => head.content == *content,
        };
        let binding = serde_json::json!({"process":head.process,
            "peer_leaf":exchange.evidence.local.local_certificate_digest,"target":head.target,
            "content":head.content,"purpose":purpose,"context_digest":context_digest,"reader_id":reader_id});
        let authority = format!(
            "{:x}",
            Sha256::digest(serde_json::to_vec(&binding).map_err(|error| error.to_string())?)
        );
        if !expected
            || head.reader_id != reader_id
            || head.authority_digest != authority
            || head.process.pid != std::process::id()
            || head.process.instance_id != self.config.instance
            || head.process.epoch != self.config.epoch
            || head.target.epoch != self.config.epoch
            || head.target.profile_identity != self.config.profile
            || head.target.inputs.binary_search(&head.content).is_err()
        {
            return Err(
                "actual qualified reader admission changed original process/input/context".into(),
            );
        }
        Ok(ControllerContentRead {
            client: self.clone(),
            exchange,
            head,
            pending: Vec::new(),
            offset: 0,
            hasher: Sha256::new(),
            returned: 0,
            complete: false,
            receipt: None,
            failed: false,
        })
    }
}

impl ControllerContentRead {
    /// Returns only the independently validated actual original close and its durable admission binding.
    pub fn completed_recovery_reader(&self) -> Result<(String, String), String> {
        let receipt = self
            .receipt
            .as_ref()
            .ok_or("actual original reader has not closed completely")?;
        let value = serde_json::to_value(receipt).map_err(|e| e.to_string())?;
        Ok((
            serde_json::to_string(&value).map_err(|e| e.to_string())?,
            self.head.authority_digest.clone(),
        ))
    }

    /// Returns the exact original qualified identity; it grants no new availability or use permission.
    pub fn content(&self) -> &ArtifactContentKey {
        &self.head.content
    }

    /// Consumes one bounded network chunk or the independently verified actual terminal receipt.
    fn next_chunk(&mut self) -> Result<(), String> {
        self.client.check_exchange(&self.exchange)?;
        let mut prefix = [0; 4];
        self.exchange
            .stream
            .read_exact(&mut prefix)
            .map_err(|error| error.to_string())?;
        let size = u32::from_be_bytes(prefix) as usize;
        if size > crate::content_store::STREAM_CHUNK_BYTES {
            return Err("original qualified read chunk exceeds its bound".into());
        }
        if size > 0 {
            if self
                .returned
                .checked_add(size as u64)
                .is_none_or(|n| n > self.head.content.byte_size)
            {
                return Err("original qualified read exceeds its exact admitted size".into());
            }
            self.pending.resize(size, 0);
            self.exchange
                .stream
                .read_exact(&mut self.pending)
                .map_err(|error| error.to_string())?;
            self.client.check_exchange(&self.exchange)?;
            self.offset = 0;
            return Ok(());
        }
        let receipt = match self.client.read::<ChildReply>(&mut self.exchange)? {
            ChildReply::ReadClosed(result) => result?,
            ChildReply::Rejected(error) => return Err(error),
            _ => return Err("actual qualified read lacks original terminal receipt".into()),
        };
        let observed = format!("sha256:{:x}", self.hasher.clone().finalize());
        if !receipt.complete
            || receipt.retention_only
            || receipt.content != self.head.content
            || receipt.reader_id != self.head.reader_id
            || receipt.bytes_read != self.returned
            || self.returned != self.head.content.byte_size
            || observed != self.head.content.content_digest
            || receipt.observed_digest != observed
            || self
                .head
                .target
                .member_stores
                .get(&self.head.process.epoch.replica_id)
                != Some(&receipt.store_owner)
        {
            return Err("actual qualified read receipt/returned bytes differ from original".into());
        }
        self.receipt = Some(receipt);
        self.complete = true;
        self.pending.clear();
        self.offset = 0;
        Ok(())
    }
}

impl Read for ControllerContentRead {
    /// Rechecks original connection/clock and returns only bytes from the bounded verified original stream.
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        if buffer.is_empty() {
            return Ok(0);
        }
        if self.failed {
            return Err(io::Error::other(
                "original qualified read is fenced after failure",
            ));
        }
        let result = (|| -> Result<usize, String> {
            self.client.check_exchange(&self.exchange)?;
            if self.complete {
                return Ok(0);
            }
            if self.offset == self.pending.len() {
                self.next_chunk()?;
            }
            if self.complete {
                return Ok(0);
            }
            let count = buffer.len().min(self.pending.len() - self.offset);
            buffer[..count].copy_from_slice(&self.pending[self.offset..self.offset + count]);
            self.offset += count;
            self.returned += count as u64;
            self.hasher.update(&buffer[..count]);
            Ok(count)
        })();
        result.map_err(|error| {
            self.failed = true;
            io::Error::other(error)
        })
    }
}
