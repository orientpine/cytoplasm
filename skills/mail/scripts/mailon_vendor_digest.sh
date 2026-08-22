# Shared content digest for the vendored mailon tree. Sourced, never executed.
#
# WHY it is shared: the runtime release directory is *named* by this digest, and the
# drift probe decides "is the node running current vendor code?" by recomputing it. If
# the two sides computed it separately they would drift, and the drift detector would be
# the thing that drifts.
#
# WHY the path is stripped: `sha256sum` prints "<hash>  <path>", so hashing its output
# directly folds the absolute directory name into the digest. That made the same bytes
# produce different digests depending on where they were unpacked — a "content digest"
# that is not addressed by content, which is exactly what a probe comparing a repo tree
# against a deployed tree cannot tolerate. LC_ALL=C keeps the sort locale-independent.

mailon_vendor_digest() {
  local tree="$1"
  [ -d "$tree" ] || return 1
  ( cd "$tree" && find . -type f -name '*.py' -print0 \
      | LC_ALL=C sort -z \
      | xargs -0 sha256sum \
      | sha256sum \
      | cut -c1-16 )
}
