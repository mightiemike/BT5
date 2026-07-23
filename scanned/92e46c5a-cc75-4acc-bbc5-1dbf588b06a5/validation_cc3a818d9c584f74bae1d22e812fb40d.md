### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual End User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the **direct caller of `pool.swap`**. When users route through `MetricOmmSimpleRouter`, `sender` equals the router address, not the actual user. If the pool admin allowlists the router (a natural action to enable router-based swaps), every user routing through the router bypasses the individual allowlist check entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then performs its allowlist check keyed on that `sender`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract**, not the originating user: [4](#0-3) 

Therefore the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`. If the pool admin has allowlisted the router address (the natural action to enable router-mediated swaps for their allowlisted users), the check passes for **every** user who routes through the router, regardless of whether that user is individually allowlisted.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` parameter (the position owner, passed explicitly by the pool from the `addLiquidity` call), not `sender`: [5](#0-4) 

The asymmetry between the two extensions confirms this is not a deliberate design choice for the swap path.

---

### Impact Explanation

Any user can swap in a pool whose `SwapAllowlistExtension` is configured to restrict access to specific addresses, simply by routing through `MetricOmmSimpleRouter`. The allowlist — intended to gate who may trade against the pool's liquidity — is rendered ineffective for all router-mediated swaps. Unauthorized users gain full swap access, which can lead to direct loss of LP principal through unrestricted arbitrage or manipulation against a pool that was designed to trade only with trusted counterparties.

---

### Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router address. This is a **natural and expected administrative action**: a pool admin who wants to allow their allowlisted users to trade via the standard router will add the router to the allowlist, not realizing that doing so grants access to all router users. The router is a public, permissionless contract. The admin has no way to restrict which users call it. The misunderstanding is structurally induced by the API: `setAllowedToSwap(pool, router, true)` reads as "allow the router," but its actual effect is "allow everyone who uses the router."

---

### Recommendation

The extension must check the **originating user**, not the direct pool caller. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a convention between router and extension.
2. **Check both `sender` and a forwarded-user field**: Extend the `beforeSwap` hook signature or use a dedicated field in `extensionData` so the extension can distinguish "router acting on behalf of user X" from "user X calling directly."

The simplest safe fix without changing the hook ABI is to **not allowlist the router** and instead require users to call the pool directly when the allowlist is active — but this must be clearly documented and enforced at the UI/integration layer, which is fragile. A code-level fix is strongly preferred.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin calls: extension.setAllowedToSwap(pool, router, true)
   // Intent: "let the router facilitate swaps for my allowlisted users"
   // Actual effect: allowedSwapper[pool][router] = true

3. Non-allowlisted user (attacker) calls:
   router.exactInputSingle(ExactInputSingleParams{
     pool: pool,
     sender: attacker,   // attacker is msg.sender of the router call
     ...
   })

4. Router calls: pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
   // pool's msg.sender = router

5. Pool calls: _beforeSwap(msg.sender=router, recipient, ...)
   // sender argument = router

6. Extension evaluates:
   allowAllSwappers[pool]           → false
   allowedSwapper[pool][router]     → true  ← bypass succeeds

7. Swap executes. Attacker receives output tokens.
   // allowedSwapper[pool][attacker] was never checked.
``` [6](#0-5) [1](#0-0) [4](#0-3)

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
