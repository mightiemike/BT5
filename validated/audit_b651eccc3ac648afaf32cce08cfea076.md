Looking at the external bug's core invariant — a privileged parameter change silently alters accounting for existing state — I need to find a sequencer analog where a conversion or version boundary silently changes the hash/fee preimage for an already-accepted transaction.

Let me trace the `ValidResourceBounds` representation boundary through the hash and protobuf layers.