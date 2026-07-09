# Q335: LTC mainnet batch-split retarget transition

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Litecoin mainnet fork with the candidate fork becoming heavier by exactly one claimed work increment, where the attacker can split the critical headers across two relayer batches and verify that the second batch cannot validate against stale predecessor assumptions from the first, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: contract/src/litecoin.rs::get_next_work_required + relayer/src/main.rs::prepare_and_submit_batches + relayer/src/near_client.rs::sign_submit_blocks
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Litecoin fork with scrypt-valid headers, crafted `bits`, `time`, and fork order around difficulty boundaries
- Exploit idea: split the critical headers across two relayer batches and verify that the second batch cannot validate against stale predecessor assumptions from the first
- Invariant to test: splitting a fork across relayer batches must not change whether the headers are valid or canonical
- Expected Immunefi impact: Contract execution flows
- Fast validation: Replay the same fork once in one batch and once split across two batches and compare acceptance and tip state.
