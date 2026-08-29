//! Link-time-elided checkpoint observation handler.

#[inline(always)]
pub(crate) fn checkpoint(_id: &[u8], _hash: &[u8], _parameters: &[u64]) {}

#[inline(always)]
pub(crate) fn range(_kind: &[u8], _index: u64, _base: u64, _end: u64) {}

#[inline(always)]
pub(crate) fn content_chunk(_class: u64, _chunk: u64, _count: u64, _lo: u64, _hi: u64) {}

#[inline(always)]
pub(crate) fn content_item(_class: u64, _index: u64, _va: u64, _pa: u64, _flags: u64) {}
