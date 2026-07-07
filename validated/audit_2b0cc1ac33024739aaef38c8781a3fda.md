### Title
Missing Event Emission on `linkedSigners` Mapping Update Prevents Off-Chain Monitoring of Signer Delegation - (File: `core/contracts/EndpointTx.sol`)

### Summary
When a user submits a `LinkSigner` transaction, the `linkedSigners` mapping is silently updated with no event emitted. Because `linkedSigners` is a non-iterable mapping, there is no on-chain mechanism for users, indexers, or monitoring tools to reconstruct the current set of linked signers or detect when a subaccount's authorized signer has changed.

### Finding Description
In `EndpointTx.sol`, the `LinkSigner` transaction type is handled at lines 576–590. After signature validation, the code writes directly to the `linkedSigners` mapping:

```solidity
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
```

No event is emitted after this write. The `linkedSigners` mapping is declared in `EndpointStorage.sol` as:

```solidity
mapping(bytes32 => address) internal linkedSigners;
```

This is a non-iterable data structure. There is no corresponding event definition anywhere in `EndpointTx.sol` or `EndpointStorage.sol` for signer link changes. The `Verifier.sol` contract, by contrast, does emit `AssignPubKey` and `DeletePubkey` events when its own key mappings change, demonstrating that the pattern is known and applied selectively.

### Impact Explanation
`linkedSigners` is a security-critical mapping: it determines which external address is authorized to sign sequenced transactions on behalf of a given subaccount. A change to this mapping is equivalent to delegating full trading authority over a subaccount. Without an event:

- Users cannot set up off-chain watchers to detect unauthorized or unexpected signer changes on their subaccounts.
- Indexers and front-ends cannot reconstruct the current linked signer for any subaccount without replaying all `LinkSigner` transactions from calldata, which is fragile and not guaranteed to be available.
- If a subaccount owner's key is compromised and an attacker submits a `LinkSigner` transaction, the victim has no reliable on-chain signal to detect the change and respond (e.g., by revoking the signer or withdrawing funds via slow-mode).

### Likelihood Explanation
Every user who calls `LinkSigner` triggers this code path. The missing event is a structural gap that affects every signer delegation, past and future. The likelihood of the gap being exploited as a monitoring blind spot is high given that linked signers are the primary authorization mechanism for sequenced transactions.

### Recommendation
Declare and emit a `LinkedSignerSet` event whenever the `linkedSigners` mapping is updated:

```solidity
event LinkedSignerSet(bytes32 indexed sender, address indexed signer);
```

Emit it immediately after the mapping write in the `LinkSigner` branch of `EndpointTx.sol`. Similarly, audit `nlpSigners` updates in `addNlpPool` and `updateNlpPool` for the same gap.

### Proof of Concept

1. User A submits a `LinkSigner` transaction delegating their subaccount to address `0xAttacker`.
2. The sequencer processes it; `EndpointTx.sol` line 588–590 writes `linkedSigners[sender] = 0xAttacker`.
3. No event is emitted.
4. An off-chain monitor watching for signer changes on User A's subaccount receives no signal.
5. `0xAttacker` now signs and submits trades on behalf of User A's subaccount with no observable on-chain trace beyond raw calldata.

**Root cause:** [1](#0-0) 

**Non-iterable mapping declaration:** [2](#0-1) 

**Contrast — `Verifier.sol` does emit events for its analogous key mapping changes:** [3](#0-2)

### Citations

**File:** core/contracts/EndpointTx.sol (L576-590)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLinkSigner)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            linkedSigners[signedTx.tx.sender] = address(
                uint160(bytes20(signedTx.tx.signer))
            );
```

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```

**File:** core/contracts/Verifier.sol (L33-34)
```text
    event AssignPubKey(uint256 i, uint256 x, uint256 y);
    event DeletePubkey(uint256 index);
```
