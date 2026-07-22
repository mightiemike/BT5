### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the actual user, allowing any user to bypass a curated pool's swap allowlist via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict which addresses may swap on a curated pool. Its `beforeSwap` hook checks the `sender` argument passed by the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. The allowlist therefore gates the router's address, not the real swapper. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unpermissioned user can bypass the allowlist by routing through the same router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every registered extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the **router**, so `sender` arriving at the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The allowlist storage is keyed and administered per-pool, per-swapper: [5](#0-4) 

The pool admin's intent is to allowlist individual users. Because the check resolves to the router's address, the admin faces an impossible choice:

- **Do not allowlist the router** → every legitimate user who tries to swap through the router is blocked, even if they are individually allowlisted.
- **Allowlist the router** → every unpermissioned user can bypass the allowlist by routing through the same public router contract.

Note that `DepositAllowlistExtension.beforeAddLiquidity` does **not** share this flaw: it ignores `sender` and checks `owner`, which is the economically relevant party for deposits: [6](#0-5) 

The swap path has no equivalent correction.

---

### Impact Explanation

A curated pool protected by `SwapAllowlistExtension` is intended to restrict trading to a known set of addresses. Once the router is allowlisted (the only way to let legitimate users trade through the standard periphery), the allowlist is effectively open to the entire public. Any user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router and the extension will pass them through. LP providers on the curated pool lose the curation guarantee they deposited under, and unauthorized traders can extract value from the pool at oracle-derived prices the pool admin did not intend to expose to them.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented swap entry point for end users. Any user who discovers the allowlist blocks their direct `pool.swap()` call will naturally try the router. The bypass requires no special knowledge, no privileged role, and no unusual token behavior — only a standard router call. Likelihood is high whenever a `SwapAllowlistExtension`-protected pool is deployed alongside the router.

---

### Recommendation

The extension must gate on the **original user**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** The router already knows `msg.sender` (the real user) at entry. Pass it as a separate field in `extensionData` or as a dedicated parameter, and have the extension decode and check it.

2. **Check `sender` only when the caller is not a known router; otherwise decode the real user from `extensionData`.** This is the pattern used by Uniswap v4 hooks with `hookData`.

The simplest production fix is to have `SwapAllowlistExtension.beforeSwap` decode the real originator from `extensionData` when `sender` is a recognized router, and fall back to checking `sender` directly for non-router callers. The router must be updated to encode `msg.sender` into `extensionData` before forwarding.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, Alice, true).
   Alice is the only allowlisted swapper.
3. Pool admin calls setAllowedToSwap(pool, router, true)
   so that Alice can use the standard router.

Attack
──────
4. Bob (not allowlisted) calls:
     router.exactInputSingle({pool: pool, ..., recipient: Bob})
   The router calls pool.swap(Bob, ...) with msg.sender = router.
5. SwapAllowlistExtension.beforeSwap receives sender = router.
   It checks allowedSwapper[pool][router] → true  ✓ (passes).
6. Bob's swap executes on the curated pool despite never being allowlisted.

Invariant broken
────────────────
allowedSwapper[pool][Bob] == false, yet Bob successfully swaps.
The allowlist check resolved to the router's allowance, not Bob's.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-19)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

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
