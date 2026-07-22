### Title
SwapAllowlistExtension Allowlist Bypassed via Router: Any User Can Swap in Restricted Pools When Router Is Allowlisted - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool sees `sender = router`. If the pool admin allowlists the router address (a natural configuration for pools that want to support router-mediated swaps), every user — including those not individually allowlisted — can bypass the guard by routing through the public router contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that identity against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`), the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
```

So `sender` arriving at the extension is the **router address**, not the originating user. The allowlist lookup becomes `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (to let their approved users swap via the standard periphery), the check passes unconditionally for every caller of the router, regardless of whether that caller is individually approved.

The `DepositAllowlistExtension` does not share this flaw: it checks `owner` (the LP-share recipient), which the liquidity adder sets to `msg.sender` of the adder call — the actual user — so the identity is preserved correctly.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers). Once the router is allowlisted — which is the only way to let approved users trade through the standard periphery — the restriction is nullified: any address can call `MetricOmmSimpleRouter` and execute swaps against the pool. Unauthorized traders can drain LP reserves at oracle-quoted prices, extract value from bins, and pay fees that were intended only for the restricted participant set. The pool's core access-control invariant is broken with direct LP-asset loss as the consequence.

---

### Likelihood Explanation

The bypass requires the pool admin to have allowlisted the router. This is the expected operational step for any pool that wants its approved users to trade through the standard periphery rather than calling the pool directly. There is no documentation warning against it, and the `setAllowedToSwap` / `setAllowAllSwappers` API gives no indication that allowlisting the router collapses the per-user gate. Any unprivileged user can then trigger the bypass with a single `exactInputSingle` call — no special setup, no flash loan, no privileged role.

---

### Recommendation

The extension must gate on the **originating user**, not the intermediary. Two viable approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted router convention.
2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the real user, but this breaks for multi-hop paths where intermediate recipients are the router itself.
3. **Dedicated router wrapper**: Deploy a thin router that re-checks the allowlist before forwarding to the pool, so the pool-level extension only needs to trust the wrapper.

The cleanest fix is option 1 with a signed or router-enforced user field in `extensionData`, combined with a check that `msg.sender` (the pool) is the expected pool for that extension slot.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is approved
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use periphery

Attack
──────
4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:      restrictedPool,
           recipient: bob,
           ...
       })

5. Router calls pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
   → pool.swap: msg.sender = router → sender = router
   → _beforeSwap(sender=router, ...)
   → SwapAllowlistExtension.beforeSwap:
         allowedSwapper[pool][router] == true  ✓  (passes)

6. Swap executes. Bob receives tokens from the restricted pool.
   Alice's LP position is drained at oracle price by an unauthorized counterparty.
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
