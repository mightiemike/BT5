### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. Because `MetricOmmPool.swap` sets `sender = msg.sender` (the direct caller of the pool), any swap routed through `MetricOmmSimpleRouter` presents the **router's address** as the swapper identity. A pool admin who allowlists the router to enable normal user flow inadvertently opens the gate to every user on-chain; a pool admin who does not allowlist the router silently breaks the router for every legitimately allowlisted user.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards that value unchanged.** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `sender` against the per-pool allowlist.**

```solidity
// sender == msg.sender of pool.swap() == router address when routed
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making itself `msg.sender`.**

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64, "", params.extensionData
);
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Result:** The extension never sees the real user's address. It sees only the router's address.

---

### Impact Explanation

Two mutually exclusive failure modes, both fund-impacting:

| Scenario | Effect |
|---|---|
| Pool admin allowlists the router (natural setup to let users swap) | Every on-chain address can bypass the allowlist by calling `exactInputSingle` through the router — the curated pool is fully open |
| Pool admin does not allowlist the router | Every individually allowlisted user is silently blocked from using the router; only direct `pool.swap` calls work, breaking the standard swap UX |

In the first scenario, unauthorized users can drain liquidity from a pool that was intended to be restricted (e.g., a private institutional pool or a pool with a specific counterparty whitelist). This is a direct loss of LP assets above Sherlock thresholds.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary public swap entrypoint documented in the protocol.
- A pool admin who deploys a curated pool with `SwapAllowlistExtension` will almost certainly also allowlist the router to give their approved users a normal UX — triggering the bypass immediately.
- No special permissions, flash loans, or unusual token behavior are required. Any EOA can call `exactInputSingle`.

---

### Recommendation

The extension must gate the **economic actor** (the human or contract that initiated the trade), not the intermediate dispatcher. Two viable approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Add an explicit `originator` field to the swap interface**: The pool accepts an `originator` address alongside `recipient`; the router sets it to its own `msg.sender`; the extension checks `originator` instead of `sender`.

The `DepositAllowlistExtension` does not share this flaw because it gates the `owner` argument (the position owner explicitly supplied by the caller), not the `sender` (the payer/dispatcher). [6](#0-5) 

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to let their approved users swap via the standard router)
  - Pool admin does NOT allowlist attacker EOA

Attack:
  1. Attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(...) — msg.sender = router
  3. _beforeSwap passes sender = router to SwapAllowlistExtension
  4. allowedSwapper[pool][router] == true → check passes
  5. Attacker's swap executes; allowlist is fully bypassed

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
