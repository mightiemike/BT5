### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any non-allowlisted user to bypass the swap allowlist by routing through `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end user. If the pool admin allowlists the router address (the only way to enable router-mediated swaps for legitimate users), every non-allowlisted user can bypass the restriction by routing through the same router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every registered extension. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

A pool admin who wants to restrict swaps to a curated set of users faces an impossible choice:

| Router allowlisted? | Effect |
|---|---|
| No | Allowlisted users cannot use `MetricOmmSimpleRouter` at all (broken core flow) |
| Yes | Every non-allowlisted user can bypass the restriction by calling through the router |

The second row is the security bypass: once the router is allowlisted (the only way to support the standard periphery path), the allowlist provides zero protection against any user who routes through it.

---

### Impact Explanation

Any non-allowlisted user can trade on a curated pool that is supposed to be restricted to a specific set of participants. This directly violates the pool's access-control invariant and constitutes a high-severity policy bypass with direct fund-impact consequences: non-permitted actors can drain liquidity, move the active bin, and extract value from LP positions in pools that were designed to be closed.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical, documented swap entry point for end users. Any pool that (a) deploys `SwapAllowlistExtension` and (b) needs to support router-mediated swaps for its allowlisted users must allowlist the router. This is not an edge case—it is the expected production configuration. The bypass is therefore reachable by any user who knows the router address, with no special privileges required.

---

### Recommendation

The extension must gate the economically relevant actor—the end user—not the intermediate contract. Two approaches:

**Option A (preferred):** Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check it instead of the raw `sender` argument.

**Option B:** Add an `originalSender` field to the `beforeSwap` hook signature so the pool can propagate the true initiator through the extension call chain, similar to how `addLiquidity` separates `owner` from `msg.sender`.

Either way, the invariant must be: the identity checked by the allowlist is the address that economically initiates and benefits from the swap, not the intermediate dispatcher.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` registered as a `beforeSwap` hook.
2. Pool admin allowlists `alice` (`allowedSwapper[pool][alice] = true`) and also allowlists the router (`allowedSwapper[pool][router] = true`) so that `alice` can use the standard periphery path.
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. The router calls `pool.swap(...)` with `msg.sender = router`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
6. `bob`'s swap executes successfully despite never being allowlisted.

The bypass requires zero special privileges: any user with the router address can exploit it the moment the router is added to the allowlist. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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
