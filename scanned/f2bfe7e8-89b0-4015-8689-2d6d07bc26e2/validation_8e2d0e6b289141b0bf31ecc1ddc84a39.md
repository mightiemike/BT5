### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is whoever called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the pool passes the router's address as `sender` to the extension. The extension then checks whether the **router** is allowlisted, not whether the **actual user** is allowlisted. If the pool admin adds the router to the allowlist (a natural step to enable router-mediated swaps for curated pools), every non-allowlisted user can bypass the gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` is:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the address the pool received as its own `msg.sender` — i.e., whoever called `pool.swap()`. [1](#0-0) 

The pool's `swap` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [2](#0-1) 

When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

The pool's `_afterSwap` similarly passes `msg.sender` (the router) as `sender`: [3](#0-2) 

---

### Impact Explanation

Two concrete broken outcomes:

**Bypass (High):** Pool admin adds the router to the allowlist so that allowlisted users can trade via the router. Any non-allowlisted address can then call `router.exactInput()` and the extension approves the swap because `allowedSwapper[pool][router] == true`. The curated pool's access control is fully defeated; any user can trade against LP funds that were deposited under the assumption that only vetted counterparties could swap.

**Broken functionality (High):** Pool admin adds individual users (Alice, Bob) to the allowlist but does not add the router. Alice calls `router.exactInput()`. The extension checks `allowedSwapper[pool][router]` which is `false` and reverts. Alice cannot use the supported periphery path even though she is explicitly allowlisted. The router is the canonical user-facing entrypoint; blocking it for allowlisted users breaks the core swap flow.

---

### Likelihood Explanation

The `SwapAllowlistExtension` is a production periphery contract deployed alongside `MetricOmmSimpleRouter` as the standard swap entrypoint. Any pool that activates the allowlist extension and expects users to trade through the router will encounter one of the two failure modes above. No privileged precondition beyond normal pool admin configuration is required. The bypass path is reachable by any unprivileged address.

---

### Recommendation

The extension must check the **original caller**, not the intermediary. Two options:

1. **Pass the real user through the router.** Have `MetricOmmSimpleRouter` encode the original `msg.sender` in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it. This requires a trusted router identity check inside the extension.

2. **Check `sender` only when `msg.sender` (the pool) is the direct caller.** If the pool's `msg.sender` is a known trusted router, decode the real user from `extensionData`; otherwise use `sender` directly.

The simplest safe fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and the extension, after verifying `msg.sender` is a trusted router, extracts and checks the real user address.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension active on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (adds the router so that router-mediated swaps are permitted for allowlisted users).
  - Alice (non-allowlisted) is NOT in allowedSwapper[pool].

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInput(...) targeting the pool.
  2. Router calls pool.swap(recipient=Alice, ...).
  3. Pool calls extension.beforeSwap(sender=router, ...).
  4. Extension evaluates: allowedSwapper[pool][router] == true → passes.
  5. Swap executes. Alice receives tokens from LP reserves.

Result:
  - Alice, who was never allowlisted, successfully swaps against a curated pool.
  - Every non-allowlisted user can repeat this. The allowlist provides zero protection
    for any user who routes through the standard periphery entrypoint.
``` [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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
