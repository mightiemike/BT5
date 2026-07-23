### Title
SwapAllowlistExtension Gates Router Address Instead of End-User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool call. When a swap is routed through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the end-user. If the pool admin allowlists the router (a natural action to enable router-mediated swaps for permitted users), every unprivileged user can bypass the swap allowlist by calling through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct — only the pool calls the extension). `sender` is the value the pool passes as the first argument, which is set in `MetricOmmPool.swap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- whoever called the pool
    recipient,
    ...
);
```

When a user calls the pool directly, `sender = user` and the check is `allowedSwapper[pool][user]` — correct.

When a user calls through `MetricOmmSimpleRouter`, the router calls the pool, so `msg.sender` of the pool = router, and `sender = router`. The check becomes `allowedSwapper[pool][router]`.

A pool admin who wants to allow router-mediated swaps for their allowlisted users must add the router to `allowedSwapper[pool][router]`. Once the router is allowlisted, **any** address — including addresses the admin explicitly excluded — can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The extension never sees the true end-user identity. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) loses that restriction entirely once the router is allowlisted. Any unprivileged user can execute swaps on the restricted pool, draining liquidity at oracle-derived prices that the LP deposited under the assumption of a controlled counterparty set. This constitutes a direct loss of LP principal and a broken core pool invariant (the allowlist guard). [3](#0-2) 

---

### Likelihood Explanation

The likelihood is **medium**. The trigger requires the pool admin to allowlist the router — a natural and expected administrative action when the pool is meant to be accessible via the standard periphery. The admin has no reason to suspect this creates a full bypass; the extension's interface gives no indication that allowlisting the router is semantically different from allowlisting a user. The `MetricOmmSimpleRouter` is a public, permissionless contract callable by anyone. [4](#0-3) 

---

### Recommendation

The extension must check the **true end-user identity**, not the immediate pool caller. Two approaches:

1. **Pass the original caller through the router**: `MetricOmmSimpleRouter` should forward the original `msg.sender` in `extensionData`, and `SwapAllowlistExtension` should decode and check that address when `sender` is a known router.

2. **Check `sender` against a router registry and unwrap**: The extension can maintain a set of trusted routers; when `sender` is a trusted router, it reads the actual user from `extensionData` and checks that address instead.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (an explicit parameter the caller provides), not on `sender` (the pool's `msg.sender`). [5](#0-4) [1](#0-0) 

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the standard periphery.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the restricted pool.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, ...)` with `sender = router`.
7. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes at oracle price, draining LP assets the depositor expected only Alice to trade against. [3](#0-2) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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
