### Title
`SwapAllowlistExtension` checks the router's address instead of the end-user's address, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct `msg.sender` of `MetricOmmPool.swap`. When users route through `MetricOmmSimpleRouter`, `sender` resolves to the router's address, not the end-user's address. A pool admin who allowlists the router (required for any router-mediated swap to function) inadvertently opens the gate to every user, completely defeating the per-user allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument: [1](#0-0) 

The check is `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded from `ExtensionCalling._beforeSwap`: [2](#0-1) 

That `sender` value originates from `MetricOmmPool.swap` as `msg.sender` of the pool call: [3](#0-2) 

When `MetricOmmSimpleRouter` calls `pool.swap(...)`, `msg.sender` of that call is the router, so `sender = router` inside `beforeSwap`. The allowlist check therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

This forces a binary, broken choice on the pool admin:

| Router allowlisted? | Effect |
|---|---|
| No | All router-mediated swaps revert, even for allowlisted users |
| Yes | Every user bypasses the per-user allowlist via the router |

There is no configuration that allows specific users through the router while blocking others.

The same structural flaw exists in `DepositAllowlistExtension.beforeAddLiquidity`, which checks `allowedDepositor[pool][owner]` (the share recipient) rather than `sender` (the token payer), allowing an unauthorized depositor to pass the gate by specifying an authorized `owner`: [4](#0-3) 

---

### Impact Explanation

The swap allowlist guard is completely ineffective for router-mediated swaps. Any non-allowlisted user can bypass the restriction by calling `MetricOmmSimpleRouter` instead of `MetricOmmPool.swap` directly. This allows unauthorized users to trade at oracle-anchored prices against LP positions. Because the pool is oracle-priced, an unauthorized arbitrageur can extract value from LPs whenever the oracle price diverges from the market, a loss that the allowlist was specifically configured to prevent.

---

### Likelihood Explanation

High. The bypass requires only that the router be allowlisted, which is a natural operational step any pool admin would take when deploying a pool intended to support router-mediated swaps. The admin may not realize that allowlisting the router is equivalent to opening the gate to all users. The router is a public periphery contract reachable by anyone.

---

### Recommendation

The extension must check the identity of the economic actor, not the intermediary. Two options:

1. **Pass the end-user address through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation and trust.
2. **Check `sender` (the router) and require the router to enforce its own per-user allowlist**: Document clearly that allowlisting the router grants access to all router users, and provide a companion router-level allowlist.

The current design silently misapplies the guard whenever an intermediary contract is in the call path.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required for router swaps
4. bob (not allowlisted) calls router.exactInput(...)
   → router calls pool.swap(recipient, ...)
   → pool calls extension.beforeSwap(router, ...)
   → check: allowedSwapper[pool][router] == true  ✓
   → swap executes; bob trades at oracle price against LP funds
5. bob calls pool.swap(...) directly
   → pool calls extension.beforeSwap(bob, ...)
   → check: allowedSwapper[pool][bob] == false  ✗  (reverts)
```

Bob is blocked on the direct path but passes freely through the router, extracting LP value the allowlist was meant to protect. [1](#0-0) [5](#0-4) [2](#0-1)

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
