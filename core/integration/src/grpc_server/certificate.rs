//! Carries the TLS-verified leaf expiry into long-lived stream and application session checks.

/// Exact bounded DER cursor; signature and chain validation remain rustls-owned.
struct Der<'a> {
    /// Immutable certificate bytes from TLS metadata.
    bytes: &'a [u8],
    /// Next unread position.
    position: usize,
}

impl<'a> Der<'a> {
    /// Reads one canonical tag/length/value without allocating from untrusted lengths.
    fn value(&mut self, tag: u8) -> Result<&'a [u8], tonic::Status> {
        let bad = || tonic::Status::unauthenticated("invalid TLS certificate validity envelope");
        if self.bytes.get(self.position) != Some(&tag) {
            return Err(bad());
        }
        self.position += 1;
        let first = *self.bytes.get(self.position).ok_or_else(bad)?;
        self.position += 1;
        let length = if first < 128 {
            usize::from(first)
        } else {
            let width = usize::from(first & 0x7f);
            if width == 0 || width > 4 {
                return Err(bad());
            }
            let end = self.position.checked_add(width).ok_or_else(bad)?;
            let bytes = self.bytes.get(self.position..end).ok_or_else(bad)?;
            if bytes[0] == 0 {
                return Err(bad());
            }
            self.position = end;
            let length = bytes
                .iter()
                .fold(0_usize, |value, byte| (value << 8) | usize::from(*byte));
            if length < 128 {
                return Err(bad());
            }
            length
        };
        let end = self.position.checked_add(length).ok_or_else(bad)?;
        let value = self.bytes.get(self.position..end).ok_or_else(bad)?;
        self.position = end;
        Ok(value)
    }
}

/// Returns the exclusive RFC 5280 notAfter time of an already TLS-verified leaf.
pub(crate) fn expires_at_ms(certificate: &[u8]) -> Result<u64, tonic::Status> {
    let bad = || tonic::Status::unauthenticated("TLS certificate time is invalid");
    let mut root = Der {
        bytes: certificate,
        position: 0,
    };
    let mut envelope = Der {
        bytes: root.value(0x30)?,
        position: 0,
    };
    let mut signed = Der {
        bytes: envelope.value(0x30)?,
        position: 0,
    };
    if signed.bytes.first() == Some(&0xa0) {
        signed.value(0xa0)?;
    }
    signed.value(0x02)?;
    signed.value(0x30)?;
    signed.value(0x30)?;
    let mut validity = Der {
        bytes: signed.value(0x30)?,
        position: 0,
    };
    let first_tag = *validity.bytes.first().ok_or_else(bad)?;
    if !matches!(first_tag, 0x17 | 0x18) {
        return Err(bad());
    }
    validity.value(first_tag)?;
    let tag = *validity.bytes.get(validity.position).ok_or_else(bad)?;
    let date = std::str::from_utf8(validity.value(tag)?).map_err(|_| bad())?;
    if !date.is_ascii() {
        return Err(bad());
    }
    let (year, rest) = match tag {
        0x17 if date.len() == 13 => {
            let year: i32 = date[..2].parse().map_err(|_| bad())?;
            (
                if year >= 50 { 1900 + year } else { 2000 + year },
                &date[2..],
            )
        }
        0x18 if date.len() == 15 => (date[..4].parse().map_err(|_| bad())?, &date[4..]),
        _ => return Err(bad()),
    };
    if !rest.ends_with('Z') || !rest[..10].bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(bad());
    }
    let number = |offset: usize| rest[offset..offset + 2].parse::<u32>().map_err(|_| bad());
    let date = chrono::NaiveDate::from_ymd_opt(year, number(0)?, number(2)?)
        .and_then(|date| date.and_hms_opt(number(4).ok()?, number(6).ok()?, number(8).ok()?))
        .ok_or_else(bad)?;
    u64::try_from(date.and_utc().timestamp_millis()).map_err(|_| bad())
}
