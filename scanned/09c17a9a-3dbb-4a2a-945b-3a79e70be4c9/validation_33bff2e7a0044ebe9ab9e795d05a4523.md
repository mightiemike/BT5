### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the pool's `swap` call. When a swap is routed through `MetricOmmSimpleRouter`, that value is the router's address, not the end user's address. A pool admin who allowlists the router to enable router-mediated swaps for their curated counterparties inadvertently opens the pool to every user on the network, because any caller can reach the pool through the same public router and pass the allowlist check.

---

### Finding Description

`SwapAllowlistExtension` is designed to restrict which addresses may trade against a pool's LP positions: [1](#0-0) 

The check on line 37 evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the caller of the extension) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap` call: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router itself calls `pool.swap(...)`: [3](#0-2) 

So `msg.sender` of the pool's `swap` is `address(router)`, and the extension receives `sender = address(router)`. The extension then checks `allowedSwapper[pool][router]` — not the identity of the actual end user.

This creates an irreconcilable dilemma for pool admins:

- **If the router is NOT allowlisted**: every user who tries to swap through the router is blocked, even those the admin intended to allow. The only path for allowlisted users is a direct pool call, which requires implementing the `IMetricOmmSwapCallback` interface themselves.
- **If the router IS allowlisted** (the natural choice to enable router-mediated swaps): the allowlist check degenerates to `allowedSwapper[pool][router] == true`, which passes for every caller regardless of their identity. Any non-allowlisted user can bypass the curated gate by routing through the public `MetricOmmSimpleRouter`.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner supplied by the caller), which is the economically relevant actor for deposits and is not substituted by the router: [4](#0-3) 

The swap path has no equivalent — the pool passes only `msg.sender` as the swapper identity, and the router is always that `msg.sender` for router-mediated swaps.

---

### Impact Explanation

**Direct loss of LP principal and fee revenue.** A curated pool using `SwapAllowlistExtension` is typically configured to trade only against known, trusted counterparties (e.g., a market-maker's own hedging bots, or a whitelist of institutional partners). If any user can bypass the allowlist via the router, they can execute arbitrage or directional trades against the pool's LP positions at oracle-anchored prices, extracting value from LPs who believed their exposure was restricted. This is a broken core pool functionality causing loss of LP assets, matching the allowed impact gate.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Pool admins who want their allowlisted users to be able to use the router (the normal UX) will allowlist the router address. The bypass requires no special privileges, no non-standard tokens, and no malicious setup — any EOA can call `exactInputSingle` on the router pointing at the curated pool.

---

### Recommendation

The extension must resolve the actual end user's identity rather than the immediate `msg.sender` of the pool. Two approaches:

1. **Pass the originating user through `extensionData`**: require allowlisted callers to include their address in `extensionData`, and have the extension verify it against `tx.origin` or a signed proof. This is fragile and adds off-chain coordination.

2. **Check `tx.origin` instead of `sender`** (simpler but has its own trade-offs for contract callers): replace `allowedSwapper[msg.sender][sender]` with `allowedSwapper[msg.sender][tx.origin]` so the check always reaches the EOA that initiated the transaction.

3. **Preferred — gate on `sender` AND require the router to forward the real user**: extend the router to embed the real payer/user in `extensionData`, and update the extension to decode and verify that identity. This preserves composability without relying on `tx.origin`.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps for their intended users. Non-router addresses (including the intended users' EOAs) are not individually allowlisted.
3. Attacker (any EOA, not on the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: curatedPool, ...})`.
4. The router calls `pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)` with `msg.sender = router`.
5. The pool calls `_beforeSwap(msg.sender=router, ...)`, which calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
6. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. The swap executes against the curated LP positions. The allowlist is fully bypassed. [5](#0-4) [3](#0-2) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
