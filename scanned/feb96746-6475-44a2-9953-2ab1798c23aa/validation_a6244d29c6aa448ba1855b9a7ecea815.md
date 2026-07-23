### Title
`SwapAllowlistExtension` checks the router address as `sender`, not the actual user — any unprivileged caller can bypass the swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is the caller of `pool.swap()`, `sender` equals the router address, not the end user. A pool admin who allowlists the router so that their permitted users can swap through it simultaneously opens the gate for every unprivileged user to bypass the allowlist by routing through the same public contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check against the `sender` parameter:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`sender` is populated by `MetricOmmPool.swap()` as `msg.sender` of that call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- this is the router, not the end user
    recipient,
    ...
);
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The pool admin faces an inescapable dilemma:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all — broken UX |
| Router **allowlisted** | Every unprivileged user can bypass the allowlist by calling the router |

There is no configuration that simultaneously allows legitimate users to use the router and blocks non-allowlisted users. The guard checks the wrong identity layer.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or protocol-controlled addresses) can have that restriction fully bypassed by any unprivileged user calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The non-allowlisted user executes a real swap against the pool's liquidity, extracting output tokens and paying input tokens through the router callback — identical in economic effect to a direct allowlisted swap. LP funds are exposed to counterparties the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it with any pool address and any `extensionData`. The only prerequisite for the bypass is that the router address appears in `allowedSwapper[pool][router]`, which is a natural operational step any pool admin would take when deploying a permissioned pool that is also meant to be accessible via the standard periphery. The bypass requires zero privileged access and zero special setup beyond the router being allowlisted.

---

### Recommendation

The `beforeSwap` hook receives both `sender` (immediate caller of `pool.swap()`) and `recipient`. Neither alone identifies the true end user when a router intermediary is involved. Two complementary fixes:

1. **Pass the real user through `extensionData`**: The router can encode `msg.sender` into `extensionData` and the extension can decode and verify it. This requires the extension to trust the router's self-reported identity, which is only safe if the router itself is verified.

2. **Check `sender` and reject known intermediaries**: Explicitly block the router address from the allowlist and require users to call the pool directly for permissioned pools.

3. **Preferred — document the limitation clearly**: If the design intent is that `sender` = immediate caller, the NatSpec and admin tooling must warn that allowlisting the router opens the gate to all users, and permissioned pools must not allowlist the router.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (so that allowlisted users can use the router)
  - Alice (allowlisted) and Bob (not allowlisted) both know the pool

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       tokenIn: token0,
       tokenOut: token1,
       zeroForOne: true,
       amountIn: X,
       recipient: Bob,
       ...
     })
  2. Router calls pool.swap(Bob, true, X, ...) with msg.sender = router
  3. Pool calls _beforeSwap(router, Bob, ...)
  4. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] == true → passes
  5. Swap executes; Bob receives token1 output
  6. Bob has successfully swapped against a pool he was explicitly excluded from
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```
