# v0.6.0 frozen evidence

These nine source artifacts were recovered byte-for-byte from immutable revision
`a3be0906e162f326611bcf20f3a5f3869d2e14e9`. Each SHA256 matches the existing
`profiles/v0.6.0.freeze.json`; that historical manifest was not rewritten.
The index maps its original names to paths in a deterministic gzip/tar snapshot.
Tests read members without extracting or executing them, and work without Git
history or a network connection. Current runtime qualification is separate.

This records the revision matching the retained freeze, not a new claim about
the original deployment date or qualification beyond that manifest. Changes to
current code must not rewrite these artifacts to make tests pass. Corruption of
a payload or drift of the recorded source revision fails the maintained tests.

To audit recovery, compare `git show SOURCE_COMMIT:PATH` with the corresponding
archive member and the manifest digest. The archive contains only the nine
listed source files; no generated run logs, credentials or environment files.
