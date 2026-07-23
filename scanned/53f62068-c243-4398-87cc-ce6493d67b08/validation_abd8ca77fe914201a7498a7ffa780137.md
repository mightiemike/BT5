### Title
`SwapAllowlistExtension` checks router address as swapper, not end user — allowlist fully bypassed via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When users route through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the end user. The allowlist therefore checks the router's address, not the actual swapper. This produces two fund-impacting failure modes: (1) if the router is allowlisted, every user — including those the admin explicitly excluded — can bypass the restriction by going through the router; (2) if the router is not allowlisted, every allowlisted user is silently blocked from swapping via the standard periphery path.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every registered extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to each extension's `beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The end user's address is never forwarded to the extension. The router passes `""` as `callbackData` and the user-supplied `params.extensionData` as `extensionData`, but `SwapAllowlistExtension` ignores `extensionData` entirely and only reads `sender`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` argument (the position owner), which the pool preserves independently of who the payer/sender is: [5](#0-4) 

No equivalent "owner" field exists in the swap path, so the swap allowlist has no correct identity to check when an intermediary router is present.

---

### Impact Explanation

**Bypass path (router allowlisted):** A pool admin deploys `SwapAllowlistExtension` to restrict swaps to KYC-approved addresses and adds the router to the allowlist so that approved users can trade via the standard periphery. Because the extension checks `allowedSwapper[pool][router]` — which is `true` — every user, including those the admin explicitly excluded, can call `router.exactInputSingle(...)` and swap without restriction. The allowlist is completely defeated.

**DoS path (router not allowlisted):** A pool admin adds individual user addresses to the allowlist but does not add the router. Every swap routed through `MetricOmmSimpleRouter` reverts with `NotAllowedToSwap` regardless of whether the end user is allowlisted. The standard periphery swap path is permanently broken for all users of that pool.

Both outcomes are fund-impacting: the bypass path allows unauthorized capital flows through a pool that was configured to restrict them; the DoS path makes the pool's liquidity inaccessible to legitimate swappers via the only supported periphery router.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that deploys `SwapAllowlistExtension` and expects end-user-level access control will encounter this mismatch the first time a user routes through the router. No special attacker capability is required — a normal `exactInputSingle` call is sufficient to trigger either failure mode. The mismatch between the deposit allowlist (which correctly checks `owner`) and the swap allowlist (which checks `sender`) makes it easy for a pool admin to assume both extensions behave symmetrically, increasing the probability of misconfiguration.

---

### Recommendation

The swap allowlist must identify the actual end user, not the intermediary. Two approaches:

1. **Decode end-user identity from `extensionData`**: Have the router encode `msg.sender` into `extensionData` before calling `pool.swap()`, and have `SwapAllowlistExtension.beforeSwap` decode and check that value instead of (or in addition to) `sender`. This requires a coordinated change in both the router and the extension.

2. **Check `sender` only for direct callers; require routers to attest the user**: Define a trusted-router registry in the extension. When `sender` is a known router, require the router to have encoded the real user in `extensionData`; when `sender` is not a known router, check `sender` directly.

At minimum, the `SwapAllowlistExtension` NatSpec and the pool admin documentation must warn that `sender` equals the router address for all router-mediated swaps, so admins do not add the router to the allowlist expecting per-user granularity.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists the router so that approved users can trade via periphery.
// allowedSwapper[pool][router] = true

// Attacker (not on the allowlist) calls the router:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1_000,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// pool.swap() is called with msg.sender = router
// _beforeSwap passes sender = router to SwapAllowlistExtension
// allowedSwapper[pool][router] == true  →  check passes
// Attacker receives token1 output despite not being on the allowlist.
```

The symmetric DoS case: remove the router from the allowlist, add `attacker` directly. The attacker's direct `pool.swap()` call succeeds; their router call reverts with `NotAllowedToSwap`, blocking the standard periphery path even for an explicitly allowlisted address.

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
