All four code paths are confirmed in the repository. Here is the validation result:

---

Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the originating user, enabling full allowlist bypass — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's own `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router address (the natural action to support the official periphery on a curated pool) inadvertently opens the pool to every user, because the extension never observes the originating user identity — only the router's address.

## Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to extensions.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` unchanged into `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`: [2](#0-1) 

**Step 2 — When the router calls the pool, `msg.sender` is the router, not the user.**

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly; the pool sees `msg.sender = router`: [3](#0-2) 

The same applies to `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165).

**Step 3 — The allowlist check is keyed on the wrong identity.**

`SwapAllowlistExtension.beforeSwap` receives `sender` (the router) and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) but `sender` is the router (wrong): [4](#0-3) 

**Step 4 — Admin allowlist entries are keyed on user addresses, not the router.**

The admin configures the allowlist per user via `setAllowedToSwap`: [5](#0-4) 

A pool admin who calls `setAllowedToSwap(pool, address(router), true)` — intending to allow the official periphery while still restricting individual users — creates a single entry `allowedSwapper[pool][router] = true`. Because the extension only ever sees the router as `sender` for all router-mediated swaps, this single entry passes every non-allowlisted user through the check. There is no mechanism in the current hook interface to forward the originating user identity.

## Impact Explanation
A pool deployed with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (KYC'd counterparties, protocol-owned addresses, whitelisted market makers) is completely open to any user who routes through `MetricOmmSimpleRouter`. The pool's token balances and LP positions are exposed to unrestricted swaps, which can drain LP value through arbitrage or violate the pool's intended access model. This directly maps to the "Allowlist path" impact gate: a disallowed user can trade on a curated pool, constituting a broken core pool functionality causing potential loss of funds.

## Likelihood Explanation
High. The router is the primary supported swap path for end users. A pool admin who deploys a curated pool and wants to support the official periphery has no choice but to allowlist the router address — there is no mechanism to forward the original user identity through the current hook interface. The bypass is reachable by any user on any curated pool that supports router-mediated swaps, which is the expected production configuration. Zero privileged access is required by the attacker.

## Recommendation
The extension must gate the economically relevant actor, not the intermediary. Two viable fixes:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a convention between the router and the extension.
2. **Transient storage attestation**: The router populates a transient-storage slot with the originating user before calling the pool; the extension reads that slot from the router. This avoids calldata encoding overhead and is tamper-resistant if the slot is write-once per call.
3. **Minimal fix — document and enforce**: If the design intent is that the allowlist gates the direct caller (router or user), the admin documentation must make clear that allowlisting the router opens the pool to all users, and admins should instead allowlist individual users and require them to call the pool directly (bypassing the router).

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls:
       swapAllowlist.setAllowedToSwap(pool, address(router), true)
   (intending to allow the official periphery while still restricting users)
3. Non-allowlisted user (attacker) calls:
       router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})
4. Router calls pool.swap(...) — pool sees msg.sender = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
7. Attacker's swap executes on the curated pool despite never being allowlisted.
```

The bypass requires zero privileged access: any public user can call the public router. The only precondition is that the pool admin has allowlisted the router, which is the natural configuration for a curated pool that still wants to support the official periphery.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
