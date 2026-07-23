### Title
`SwapAllowlistExtension` gates the router intermediary instead of the actual user, enabling complete allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the actual user. This creates two irreconcilable states: either the router is not allowlisted (breaking all router-mediated swaps for legitimate users) or the router is allowlisted (granting every user on the internet a bypass of the curated allowlist).

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 231
_beforeSwap(
  msg.sender,   // ← always the direct caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// ExtensionCalling.sol line 163-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of the pool:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The actual user (`msg.sender` of `exactInputSingle`) is stored only in the router's transient callback context for payment purposes and is **never forwarded to the pool or the extension**. The pool has no mechanism to receive the originating user's address — its `swap()` signature contains no `sender` parameter.

This produces the same dual-identity confusion as the SecuritizeVault report: the term "sender" means the router in the extension's view but means the actual user in the pool admin's intent.

---

### Impact Explanation

**Scenario A — Router not allowlisted (default):**  
Pool admin allowlists specific user addresses. Those users call `exactInputSingle` → router calls `pool.swap()` → extension sees `sender = router` → `allowedSwapper[pool][router]` is `false` → **revert**. Legitimate, allowlisted users cannot use the primary periphery entry point. Core swap functionality is broken for the intended audience.

**Scenario B — Router allowlisted (to fix Scenario A):**  
Pool admin adds the router to the allowlist. Now `allowedSwapper[pool][router] = true`. Any user — including those the admin explicitly excluded — calls `exactInputSingle` → extension sees `sender = router` → check passes → **complete allowlist bypass**. The curated pool's access control is nullified for all router-mediated swaps.

Both outcomes are fund-impacting: Scenario A locks out legitimate LPs from the primary swap path; Scenario B lets unauthorized actors trade on pools that were designed to be restricted (e.g., RWA pools, KYC-gated pools, institutional pools).

---

### Likelihood Explanation

- The router (`MetricOmmSimpleRouter`) is the **primary user-facing entry point** for swaps; most users never call the pool directly.
- Pool admins who configure a `SwapAllowlistExtension` will inevitably discover that allowlisted users cannot swap through the router and will add the router to the allowlist as the natural fix — triggering Scenario B.
- No privileged access is required: any user can call `exactInputSingle` on a pool whose router is allowlisted.
- The bypass is a single public function call with no special preconditions.

---

### Recommendation

The extension must gate the **originating user**, not the intermediary. Two viable approaches:

1. **Decode the real user from `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it. The extension should also verify that `sender` (the direct pool caller) is a recognized periphery contract before trusting the decoded address.

2. **Check `sender` only when it is not a recognized router**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, decode the real user from `extensionData`; otherwise check `sender` directly.

Either approach eliminates the ambiguity between "the contract that called `pool.swap()`" and "the economic actor the pool admin intended to gate."

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists the router so that legitimate users can swap via the router.
// Attacker (not allowlisted) exploits the bypass:

function testSwapAllowlistBypassViaRouter() public {
    // Admin allowlists the router (necessary for any router-mediated swap to work)
    vm.prank(poolAdmin);
    swapAllowlistExtension.setAllowedToSwap(address(pool), address(router), true);

    // Attacker is NOT individually allowlisted
    assertFalse(swapAllowlistExtension.isAllowedToSwap(address(pool), attacker));

    // Attacker swaps through the router — extension sees sender=router, which IS allowlisted
    vm.prank(attacker);
    token0.approve(address(router), type(uint256).max);
    // This succeeds despite attacker not being in the allowlist
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token0),
            recipient: attacker,
            zeroForOne: true,
            amountIn: 1e18,
            amountOutMinimum: 0,
            priceLimitX64: 0,
            deadline: block.timestamp,
            extensionData: ""
        })
    );
    // Attacker received token1 despite not being allowlisted — allowlist bypassed
}
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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

    _clearExpectedCallbackPool();
  }
```
