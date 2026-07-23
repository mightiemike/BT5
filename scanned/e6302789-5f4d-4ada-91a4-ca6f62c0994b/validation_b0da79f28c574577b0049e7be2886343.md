### Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender` — the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` resolves to the **router's address**, not the originating user. If the pool admin allowlists the router (which is required for any allowlisted user to use the router), every non-allowlisted user can bypass the per-user swap gate by routing through the same public router contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the value the pool forwards from its own `swap()` call. The pool passes `msg.sender` of `swap()` as `sender` to `ExtensionCalling._beforeSwap`, which then encodes it verbatim into the extension call:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, amountSpecified, ...)
    )
);
```

When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool sees `msg.sender = router`, so `sender = router` is what the extension receives. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

This creates an irresolvable dilemma for pool admins who want to restrict swaps to specific users while still allowing those users to use the router:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user — allowlisted or not — can bypass the per-user gate |

The existing integration test in `FullMetricExtension.t.sol` confirms the identity model: the allowlist entry must be set for `address(callers[0])` (the direct caller / intermediate contract), not `users[0]` (the originating EOA), which is exactly the pattern that breaks when the router is the intermediate contract.

---

### Impact Explanation

Any non-allowlisted user can swap on a pool that is intended to be restricted (e.g., KYC-gated, institutional-only, or regulatory-compliance pools) simply by calling `MetricOmmSimpleRouter.exactInput` / `exactOutput`. The allowlist extension's core invariant — "only approved addresses may swap" — is silently voided for all router-mediated paths. Unauthorized swaps drain LP liquidity and generate fees from actors the pool admin explicitly excluded, constituting a broken core pool access-control invariant with direct fund-flow consequences for LPs.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any user can call it. The only precondition is that the pool admin has allowlisted the router (a natural operational step to make the pool usable for legitimate users). No privileged access, special tokens, or malicious setup is required.

---

### Recommendation

The router should propagate the originating user's address to the pool so the extension can gate on the economically relevant actor. One approach: add an explicit `sender` parameter to the pool's `swap()` interface (validated against `msg.sender` or a trusted forwarder registry), and have the router pass the original `msg.sender` through. Alternatively, the `SwapAllowlistExtension` could check both the direct caller **and** an optional `sender` field decoded from `extensionData`, letting the router supply the real user identity in the payload.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls:
       setAllowedToSwap(pool, user1, true)       // legitimate user
       setAllowedToSwap(pool, router, true)       // required so user1 can use the router
3. user2 (NOT allowlisted) calls:
       MetricOmmSimpleRouter.exactInput({ ..., path: [token0, pool, token1], recipient: user2 })
4. Router calls pool.swap(recipient=user2, ...)
       → pool records sender = address(router)
       → ExtensionCalling._beforeSwap forwards sender=router to SwapAllowlistExtension
       → extension checks allowedSwapper[pool][router] → true → no revert
5. user2's swap executes successfully despite never being allowlisted.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
