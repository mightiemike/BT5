### Title
`SwapAllowlistExtension` Gates the Wrong Actor (`sender`/Router) Instead of the Economic Beneficiary (`recipient`/User), Breaking Curated-Pool Swap Access Control — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool binds to `msg.sender` of `pool.swap()`. When the standard `MetricOmmSimpleRouter` is the caller, `sender` is the **router address**, not the end user. This produces two fund-impacting failure modes: (1) allowlisted users cannot swap through the router because the router is not on the allowlist; (2) if the router is allowlisted, every user — including non-allowlisted ones — can bypass the per-user gate. The sibling `DepositAllowlistExtension` correctly checks `owner` (the user), confirming the asymmetry is unintentional.

---

### Finding Description

**Pool → Extension actor binding**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value as the first argument (`sender`) to every configured extension: [2](#0-1) 

**SwapAllowlistExtension checks `sender`, not `recipient`**

```solidity
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [3](#0-2) 

`msg.sender` inside the extension is the pool; `sender` is whoever called `pool.swap()`.

**Router passes itself as `sender`**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` with `msg.sender = router`: [4](#0-3) 

So the extension sees `sender = router`, never the end user.

**Contrast: DepositAllowlistExtension correctly checks `owner`**

```solidity
function beforeAddLiquidity(address, address owner, ...)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [5](#0-4) 

`owner` is the position owner (the user), not the payer/intermediary. The deposit guard correctly identifies the economic actor regardless of which intermediary calls `addLiquidity`. The swap guard does not.

---

### Impact Explanation

**Failure mode A — Broken core swap functionality (certain)**

A pool admin configures `SwapAllowlistExtension` and allowlists specific user EOA addresses. Those users call `router.exactInputSingle(...)`. The router calls `pool.swap(recipient=user, ...)` with `msg.sender = router`. The extension checks `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap`. Allowlisted users cannot use the standard periphery router; the swap flow is unusable for the intended audience.

**Failure mode B — Allowlist bypass (triggered by plausible admin config)**

A pool admin allowlists the router address as a trusted intermediary (a natural choice: "let the router through, it handles its own auth"). Any non-allowlisted user calls `router.exactInputSingle(pool, ...)`. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds. The per-user allowlist is fully bypassed. On a KYC-gated or restricted-counterparty pool this allows unauthorized parties to drain LP liquidity at oracle-derived prices.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the documented standard periphery entry point for swaps. Any pool that deploys `SwapAllowlistExtension` to gate individual users will immediately encounter failure mode A the first time an allowlisted user tries the router. Failure mode B is triggered whenever the admin allowlists the router address — a natural and expected configuration for a pool that wants to support both direct and router-based access. Both paths are reachable by ordinary, unprivileged users with no malicious setup required.

---

### Recommendation

Change `SwapAllowlistExtension.beforeSwap` to gate on `recipient` (the second parameter, the economic beneficiary) instead of `sender` (the direct caller):

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

This mirrors the design of `DepositAllowlistExtension`, which correctly checks `owner` (the user) rather than the payer/intermediary. With this fix, the allowlist enforces the same policy regardless of whether the user calls the pool directly or through the router.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `swapExtension.setAllowedToSwap(pool, router, true)` — allowlisting the router as a trusted intermediary.
3. Non-allowlisted attacker calls `router.exactInputSingle({pool, recipient: attacker, ...})`.
4. Router calls `pool.swap(recipient=attacker, ...)` with `msg.sender = router`.
5. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap executes.
6. Attacker receives output tokens from the curated pool despite never being individually allowlisted.

Alternatively, to demonstrate failure mode A:

1. Admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — allowlisting Alice by EOA.
2. Alice calls `router.exactInputSingle({pool, recipient: alice, ...})`.
3. Extension evaluates `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap`.
4. Alice's swap reverts even though she is explicitly allowlisted.

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
