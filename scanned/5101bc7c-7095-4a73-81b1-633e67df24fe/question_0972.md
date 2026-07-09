# Q972: ZEC testnet prev-block-mtp reuse after reorg

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Zcash testnet fork while downstream bridge logic is waiting for the first proof on the attacker branch, where the attacker can trigger a short heavier reorg inside the averaging window so the contract may reuse median-time data from the displaced branch when evaluating the new tip, so that the contract accepts a Zcash header or branch that the source chain would reject and downstream bridge logic trusts a false canonical state?

## Target
- File/function: contract/src/zcash.rs::zcash_get_next_work_required + contract/src/lib.rs::reorg_chain
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Zcash-testnet fork with chosen header bytes, Equihash solution, testnet min-difficulty timing, and branch order
- Exploit idea: trigger a short heavier reorg inside the averaging window so the contract may reuse median-time data from the displaced branch when evaluating the new tip
- Invariant to test: a reorg must fully invalidate cached or implied MTP state from the displaced branch before validating the new canonical tip
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Reorg inside the averaging window and assert the contract recomputes MTP entirely from the new branch.
