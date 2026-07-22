Looking at the external bug pattern — a variable used before it's set to the correct value, causing a default/zero value to be used in a critical computation — I need to find an analog in the sequencer's RPC/internal conversion or hash domain.

Let me trace the `BroadcastedDeclareTransaction::V1` → `ExecutableTransactionInput` conversion path used by `estimate_fee`.