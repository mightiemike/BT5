### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the `pool.swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `sender` equals the **router address**, not the actual end user. If a pool admin allowlists the router (the natural action to enable router-mediated swaps for legitimate users), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Call chain — direct swap (correct):**

```
User → pool.swap()
  pool: _beforeSwap(msg.sender=User, ...)
  extension: allowedSwapper[pool][User]  ← correct actor checked
```

**Call chain — router-mediated swap (broken):**

```
User → router.exactInputSingle()
  router: pool.swap(recipient, ...)      ← router is msg.sender to pool
  pool: _beforeSwap(msg.sender=Router, ...)
  extension: allowedSwapper[pool][Router] ← wrong actor checked
```

In `MetricOmmPool.swap`, the pool unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When the call originates from `MetricOmmSimpleRouter.exactInputSingle`, the router is `msg.sender` to the pool: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`. If the router is allowlisted (the only way to permit router-mediated swaps for legitimate users), the check passes for **every caller of the router**, regardless of whether that caller is on the allowlist.

The extension's own documentation states it "Gates `swap` by swapper address, per pool": [5](#0-4) 

The intended gated actor is the economic swapper (the end user), not the intermediary router.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma:

| Admin choice | Consequence |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router; they must call the pool directly |
| **Allowlist the router** | Every unprivileged user bypasses the allowlist via the router |

If the admin takes the natural path (allowlisting the router so that legitimate users can use the standard periphery), any non-allowlisted address can call `router.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and trade on the curated pool. The pool's curation policy is completely nullified. Depending on the pool's purpose (e.g., institutional-only pricing, regulatory KYC gate, or preferential LP terms), this constitutes a direct loss of LP assets or unauthorized extraction of favorable pricing — a broken core pool invariant above Sherlock thresholds.

---

### Likelihood Explanation

Likelihood is **medium-high**. Allowlisting the router is the expected operational step for any pool admin who wants their allowlisted users to access the standard swap UX. The protocol provides no warning that doing so opens the bypass. The router is a public, permissionless contract, so any attacker can exploit the bypass immediately after the admin allowlists it.

---

### Recommendation

The extension must gate the **actual end user**, not the intermediary. Two sound approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Introduce a `realSender` field in the pool's hook arguments**: The pool could accept an explicit `realSender` parameter (separate from `msg.sender`) that the router populates with the original caller. The extension then checks `allowedSwapper[pool][realSender]`.

3. **Document that the router must never be allowlisted** and provide a dedicated router wrapper that enforces per-user allowlist checks before calling the pool — keeping the extension's current logic but moving the gate upstream.

---

### Proof of Concept

```
Setup:
  pool = new MetricOmmPool(...) with SwapAllowlistExtension configured
  admin.setAllowedToSwap(pool, alice, true)      // alice is a legitimate user
  admin.setAllowedToSwap(pool, router, true)     // admin allowlists router for UX

Attack:
  // bob is NOT on the allowlist
  bob calls router.exactInputSingle({
      pool: pool,
      tokenIn: token0,
      zeroForOne: true,
      amountIn: 1_000_000,
      recipient: bob,
      ...
  })

  // router calls pool.swap(bob_recipient, ...) — router is msg.sender to pool
  // pool calls _beforeSwap(sender=router, ...)
  // extension checks allowedSwapper[pool][router] == true  → PASSES
  // bob receives token1 output; allowlist is bypassed
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
