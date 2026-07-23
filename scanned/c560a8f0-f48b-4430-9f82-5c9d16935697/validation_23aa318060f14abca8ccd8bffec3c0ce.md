### Title
Signature Replay in `allowPushers` Lets Creator Re-establish Pusher Delegation After Revocation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies a pusher's EIP-191 consent signature but never marks it as consumed. Within the deadline window a creator can replay the identical calldata after the pusher has called `revokePusher()`, silently re-establishing delegation and preventing the pusher from ever effectively revoking until the deadline expires. Because the delegated namespace feeds live pool pricing, stale or unwanted oracle data can reach production swaps.

---

### Finding Description

`allowPushers` constructs and verifies the following digest:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

After a successful call it writes `namespaceRemapping[pusher] = msg.sender` and emits an event, but it does **not** record the signature hash as spent. [2](#0-1) 

`revokePusher()` clears the mapping to `address(0)`: [3](#0-2) 

Because the signature is not consumed, the creator can immediately re-submit the original `allowPushers` calldata (same `deadline`, same `signatures` bytes) after every revocation. The code's own comment acknowledges the replay concern and names the deadline as the intended guard:

> *"an undated signature could re-establish a delegation AFTER the pusher revoked it"* [4](#0-3) 

The deadline prevents replay **after** it expires, but within the window the same signature is accepted an unlimited number of times — exactly the Payment.sol pattern where a signed action can be replayed until a time boundary is reached.

---

### Impact Explanation

Every `fallback()` push from the pusher resolves its target namespace through `namespaceRemapping[msg.sender]`: [5](#0-4) 

While the delegation is forcibly kept alive, any data the pusher pushes — even data they believe is going to their own namespace — lands in the creator's namespace. If the creator's namespace is the `feedId` source for a registered pool price provider, that data drives live bid/ask quotes. A pusher who has revoked because their data source is degraded or stale cannot stop their data from reaching pool swaps until the deadline expires, satisfying the **bad-price execution** impact criterion.

---

### Likelihood Explanation

- The creator has a direct financial incentive to maintain oracle attribution (their pool's pricing depends on it).
- The original `allowPushers` calldata is a public on-chain transaction; no off-chain secret is needed to replay it.
- The pusher has no on-chain mechanism to invalidate the signature before the deadline; `revokePusher` is immediately overridable.
- The pusher may not monitor `PusherAuthorized` events and may not realize their revocation is being overridden.

---

### Recommendation

Record each verified signature hash as spent and revert on reuse:

```solidity
mapping(bytes32 => bool) private _usedDelegationHashes;

// inside allowPushers, after ECDSA.recover succeeds:
require(!_usedDelegationHashes[hash], "signature already used");
_usedDelegationHashes[hash] = true;
```

Alternatively, include a per-pusher monotonic nonce in the signed digest and increment it on each successful delegation, so a revoked pusher can invalidate all prior signatures by incrementing their nonce.

---

### Proof of Concept

```
1. Pusher signs: hash = keccak256(abi.encode(chainid, oracle, deadline=T+1day, pusher, creator))
2. Creator calls allowPushers(T+1day, [pusher], [sig])
   → namespaceRemapping[pusher] = creator  ✓

3. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  ✓ (pusher believes they are free)

4. Creator replays the SAME calldata: allowPushers(T+1day, [pusher], [sig])
   → _ensureDeadline passes (deadline not yet expired)
   → ECDSA.recover returns pusher  ✓
   → namespaceRemapping[pusher] = creator  (delegation silently restored)

5. Steps 3–4 repeat until block.timestamp > T+1day.
   During this window every fallback() push from pusher lands in creator's namespace.
   If creator's namespace feeds a pool price provider, the pool executes swaps at
   pusher-supplied prices the pusher intended to stop providing.
``` [6](#0-5) [7](#0-6)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-212)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
        _ensureDeadline(deadline);

        uint256 l = pushers.length;
        require(l == signatures.length);
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];

            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
        }
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L238-243)
```text
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-317)
```text
    fallback() override external {
        uint256 end;
        uint256 namespace;

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

```
