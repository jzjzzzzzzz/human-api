# Architecture and state machine

Question states are `pending`, `claimed`, `answered`, `expired`, and `cancelled`. The database is authoritative.

Claims use one conditional `UPDATE ... WHERE status='pending' AND expires_at > now RETURNING ...`. Answers use one conditional update requiring a live claim, ownership (or admin), no prior answer, and a live overall deadline. Accepted answers cannot be overwritten.

The external request never holds a database transaction while waiting. It polls only its question row with asynchronous sleeps. This remains correct across processes and after dropped browser polling events. Queue reads lazily release expired claim leases and mark passed deadlines expired.
