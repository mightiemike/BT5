### Title
SwapAllowlistExtension checks router address instead of end-user, allowing any user to bypass the per-user swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` as the direct `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. The allowlist check therefore gates on the router's address, not the actual trader's address. Any user can bypass a per-user swap allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`).

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the end user. The allowlist check becomes `allowedSwapper[pool][router]`. Two broken outcomes follow:

1. **Router not allowlisted**: every user who goes through the router is blocked, making the router unusable for that pool even for legitimately allowlisted users.
2. **Router allowlisted** (the only way to enable router-based trading): every user — including those explicitly not on the allowlist — can swap freely by calling the router.

The extension's stated purpose is to gate `swap` by swapper address, per pool: [5](#0-4) 

That invariant is broken for all router-mediated swaps.

---

### Impact Explanation

The `SwapAllowlistExtension` is the production access-control guard for pools that restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors). A bypass means:

- Any address can trade on a pool the admin intended to restrict.
- Funds in the pool are exposed to actors the admin explicitly excluded.
- Protocol fees and LP assets accrue from trades that should have been rejected, potentially violating regulatory or contractual obligations and exposing LPs to counterparty risk they did not consent to.

This matches the allowed impact gate: **admin-boundary break — factory/pool admin access control bypassed by an unprivileged path**.

---

### Likelihood Explanation

- Trigger requires only a standard call to `MetricOmmSimpleRouter.exactInputSingle` — no special role, no privileged setup, no non-standard token.
- The router is the canonical entry point for all end-user swaps; any user who knows the pool address can exploit this.
- The pool admin has no on-chain mechanism to distinguish router-mediated calls from direct calls at the extension layer.

Likelihood: **High**.

---

### Recommendation

Pass the actual end-user address through the swap call chain so the extension can check it. Two options:

1. **Preferred — add a `payer` / `originator` field to the swap callback context** and forward it as an additional parameter to `beforeSwap`. The router already tracks the real payer in `_setNextCallbackContext`; expose it in the extension call.

2. **Minimal fix** — document that `SwapAllowlistExtension` is incompatible with any intermediary router and require pools using it to be called directly. Add a factory-level guard that prevents registering the extension alongside a router-facing pool configuration.

---

### Proof of Concept

```
Setup:
  1. Deploy a pool with SwapAllowlistExtension configured (beforeSwap order enabled).
  2. Pool admin calls setAllowedToSwap(pool, alice, true) — only alice is allowed.
  3. Pool admin calls setAllowedToSwap(pool, router, true) — router must be allowlisted
     for any router-based trading to work.

Attack:
  4. Bob (not on the allowlist) calls MetricOmmSimpleRouter.exactInputSingle(
       pool, tokenIn, tokenOut, zeroForOne, amountIn, 0, bob, deadline, 0, ""
     ).
  5. Router calls pool.swap(bob, zeroForOne, amount, priceLimit, "", "").
  6. Pool calls extension.beforeSwap(router_address, bob, ...).
  7. Extension checks allowedSwapper[pool][router_address] == true → passes.
  8. Bob's swap executes despite not being on the allowlist.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L281-295)
```text
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-83)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);
```
