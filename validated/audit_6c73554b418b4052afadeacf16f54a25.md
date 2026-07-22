### Title
`SwapAllowlistExtension.beforeSwap` checks the direct pool caller (`sender` = router) instead of the actual end-user, making the allowlist bypassable through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address the pool passes as `msg.sender` of its own `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's allowlist entry — not the actual user's. If the pool admin allowlists the router to enable router-mediated swaps, every user on the network can bypass the curated allowlist by routing through the router.

---

### Finding Description

**Pool `swap()` passes `msg.sender` as `sender` to the extension:** [1](#0-0) 

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(); equals router when routed
    recipient,
    ...
);
```

**`ExtensionCalling._beforeSwap` forwards that value unchanged:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][sender]`:** [3](#0-2) 

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

When a user calls `MetricOmmSimpleRouter.exact*()`, the router calls `pool.swap()`. Inside the pool, `msg.sender` = router, so `sender` forwarded to the extension = router. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` (the position owner, not the operator/payer): [4](#0-3) 

The deposit extension gates the economically relevant actor (`owner`). The swap extension gates the wrong actor (`sender` = router) when the router is in the call path.

---

### Impact Explanation

Two failure modes arise:

1. **Allowlist bypass (higher severity):** The pool admin allowlists the router so that legitimate users can swap through the periphery. Because the check is `allowedSwapper[pool][router]`, every user on the network — including those the admin explicitly excluded — can bypass the curated allowlist by routing through `MetricOmmSimpleRouter`. The allowlist guard is rendered completely ineffective for router-mediated swaps.

2. **Legitimate users blocked (lower severity):** If the pool admin does not allowlist the router, allowlisted users cannot use the router at all, breaking the supported periphery swap path for curated pools.

Scenario 1 is the fund-impacting case: a curated pool designed to restrict toxic flow, front-runners, or non-KYC'd addresses is fully open to any user who routes through the public router.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary supported swap entrypoint for end users.
- A pool admin who configures a `SwapAllowlistExtension` and wants to support router-mediated swaps must allowlist the router — a natural and expected action.
- Once the router is allowlisted, the bypass is unconditional and requires no special privileges or unusual inputs from the attacker.

---

### Recommendation

The pool's `swap()` function has no parameter for the originating user; it uses `msg.sender` as the sender. Two remediation paths exist:

1. **Pass the real user through `extensionData`:** Have `MetricOmmSimpleRouter` encode the originating user's address in `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router.

2. **Introduce a `swapper` parameter to `pool.swap()`:** Add an explicit `address swapper` argument (defaulting to `msg.sender` for direct calls) so the pool can forward the true economic actor to extensions without relying on `msg.sender`.

Either fix must ensure the checked identity is the same actor the pool admin intended to gate, regardless of which supported periphery entrypoint is used.

---

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Pool admin allowlists alice (address A) to swap.
3. Pool admin allowlists MetricOmmSimpleRouter (address R) to enable router-mediated swaps.
4. Bob (address B, NOT allowlisted) calls MetricOmmSimpleRouter.exactInput(..., pool, ...).
5. Router calls pool.swap(recipient=Bob, ...) → msg.sender = Router (R).
6. Pool calls _beforeSwap(sender=R, ...).
7. SwapAllowlistExtension checks allowedSwapper[pool][R] → true (router is allowlisted).
8. Bob's swap succeeds despite Bob not being on the allowlist.
```

The check `allowedSwapper[pool][sender]` where `sender = R` is the direct analog of the external report's `state.token0 == state.token1`: a condition that evaluates against the wrong variable, making the intended guard permanently ineffective for the router-mediated path.

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
