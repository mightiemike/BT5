### Title
`SwapAllowlistExtension` gates on the immediate pool caller (`sender`) rather than the originating user, allowing any user to bypass the allowlist by routing through `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, `sender` equals the router address, not the originating user. A pool admin who allowlists the router to enable legitimate users to swap through the supported periphery path inadvertently opens the pool to every user, defeating the allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and uses it as the identity to check against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` (the immediate caller of the pool) as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // ← original user identity is never forwarded
);
``` [4](#0-3) 

The extension therefore sees `sender = router address` for every router-mediated swap. The pool admin faces an inescapable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user on-chain can bypass the allowlist by calling the router |

There is no configuration that simultaneously allows legitimate users to use the router and blocks non-allowlisted users. The allowlist guard is configured and appears active, but it is structurally bypassed on the supported periphery path — a direct analog to the external report's "guard inherited and referenced but never actually enforceable" pattern.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the position owner), which is the economically relevant actor for deposits and is preserved through the liquidity adder path: [5](#0-4) 

The swap extension has no equivalent design — it checks the immediate caller, not the originating user.

---

### Impact Explanation

Any non-allowlisted user can swap on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) after the pool admin has allowlisted the router. The allowlist's purpose — restricting swap access to vetted counterparties (e.g., KYC-gated, institutional, or low-toxicity-flow pools) — is completely defeated. LP funds are exposed to toxic flow from arbitrary users, causing direct loss of LP principal through adverse selection.

---

### Likelihood Explanation

The router is the primary supported swap interface for end users. Any pool admin who deploys a curated pool and wants their allowlisted users to access it through the standard periphery must allowlist the router. This is a routine operational step, not an exotic misconfiguration. The bypass is then reachable by any on-chain address with no special privileges.

---

### Recommendation

The extension must gate on the originating user, not the immediate pool caller. Two viable approaches:

1. **Pass originating user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a coordinated convention between router and extension.

2. **Check `sender` only when it is not a known router**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, require the originating user's address to be supplied and verified via `extensionData`.

3. **Mirror the deposit pattern**: Gate on a caller-supplied `swapper` identity (analogous to `owner` in deposits) rather than on `sender`, and require the router to forward the originating user explicitly.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in beforeSwap slot
  admin allowlists Alice:  allowedSwapper[pool][alice] = true
  admin allowlists router: allowedSwapper[pool][router] = true
    (required so Alice can use the router)

Attack:
  Charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: charlie, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=charlie, ...)   [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓ PASSES
        → swap executes, charlie receives tokens

Result: Charlie swaps successfully on a pool that should have blocked him.
        The allowlist guard was configured and active but structurally bypassed
        through the supported periphery path.
```

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
