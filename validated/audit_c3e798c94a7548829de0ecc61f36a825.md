### Title
`SwapAllowlistExtension::beforeSwap` checks the router's address instead of the actual end-user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. If the router is allowlisted (which is required for any router-mediated swap to work), every user on the network can bypass the curated pool's swap allowlist.

---

### Finding Description

**Root cause — wrong actor binding in the hook:**

`MetricOmmPool::swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← pool's caller, NOT the end-user when routed
    recipient,
    ...
);
```

`ExtensionCalling::_beforeSwap` forwards that value unchanged to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = router when routed
)
```

`SwapAllowlistExtension::beforeSwap` then checks that forwarded `sender` against the per-pool allowlist:

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`.

**The bypass path:**

`MetricOmmSimpleRouter::exactInputSingle` (and `exactInput`, `exactOutput`, `exactOutputSingle`) calls `pool.swap()` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The pool therefore sees `msg.sender = router`. The extension checks `allowedSwapper[pool][router]`. Because the router is a public, permissionless contract, the pool admin must allowlist the router address to support any router-mediated swap. Once the router is allowlisted, every user who calls `router.exactInputSingle(...)` passes the check regardless of their own allowlist status.

**Concrete scenario:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses.
2. Admin allowlists three KYC'd users and also allowlists the router so those users can trade via the standard UI.
3. Any non-KYC'd user calls `router.exactInputSingle({pool: curatedPool, ...})`.
4. The pool passes `msg.sender = router` to the extension; the extension sees `allowedSwapper[pool][router] = true` and permits the swap.
5. The non-KYC'd user executes a full swap against the curated pool's LP funds.

---

### Impact Explanation

Any user can drain or trade against a curated pool's LP assets without being on the allowlist. The pool admin's intended access control is completely nullified for all router-mediated paths. LP principal is directly at risk because unauthorized swappers can extract value at oracle-anchored prices with no slippage protection beyond the pool's own math.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any user can call it. Pool admins who want their allowlisted users to trade via the standard UI must allowlist the router, which is the natural and expected configuration. The bypass requires no special privileges, no flash loans, and no multi-step setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must check the economically relevant actor, not the intermediary. Two options:

1. **Pass the original initiator through the router.** The router already stores the real payer in transient storage (`_getPayer()`). The pool could forward an additional `initiator` field to extensions, or the extension could read it from a trusted router registry.

2. **Gate on `recipient` or require direct pool calls for allowlisted pools.** Document that pools using `SwapAllowlistExtension` must not allowlist the router and must require direct `pool.swap()` calls, so `msg.sender` is always the actual user.

The cleanest fix is option 1: the pool should pass the original `msg.sender` of the outermost public call (the real user) to extensions, not the immediate caller of `pool.swap()`.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists the router so KYC users can trade via UI
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not KYC'd, not individually allowlisted) bypasses the gate:
address attacker = makeAddr("attacker");
deal(address(token1), attacker, 10_000);
vm.startPrank(attacker);
token1.approve(address(router), type(uint256).max);

// This succeeds — extension sees sender=router, which IS allowlisted
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(pool),
    tokenIn:         address(token1),
    tokenOut:        address(token0),
    zeroForOne:      false,
    amountIn:        1_000,
    amountOutMinimum: 0,
    recipient:       attacker,
    deadline:        block.timestamp + 1,
    priceLimitX64:   type(uint128).max,
    extensionData:   ""
}));
// Attacker receives token0 from the curated pool despite not being allowlisted
vm.stopPrank();
```

The `SwapAllowlistExtension::beforeSwap` check at line 37 evaluates `allowedSwapper[pool][router]` (true) and does not revert, completing the unauthorized swap. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
