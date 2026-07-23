### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router (necessary for any legitimate user to use it), every unprivileged user can bypass the per-user swap allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct), and `sender` is the value the pool forwarded. In `ExtensionCalling._beforeSwap`, the pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← pool's msg.sender, i.e. whoever called pool.swap()
    recipient,
    ...
``` [2](#0-1) 

`ExtensionCalling` then forwards this value unchanged to the extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any multi-hop variant) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
``` [4](#0-3) 

So the extension receives `sender = address(router)` and evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][end_user]`. The actual human initiating the swap is never checked.

---

### Impact Explanation

A pool admin deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses. To allow those users to also route through `MetricOmmSimpleRouter` (the canonical periphery path), the admin must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every caller of the router, regardless of whether that caller is on the per-user allowlist. Any unprivileged user can then execute swaps on the curated pool by calling `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router. This is a complete bypass of the intended per-user access control, constituting a curation failure with direct fund-impact consequences (unauthorized parties can trade against restricted LP positions).

---

### Likelihood Explanation

The bypass is active whenever the router is allowlisted for a pool that uses `SwapAllowlistExtension`. This is the expected operational configuration: without allowlisting the router, no user can use the standard periphery path on a curated pool, making the router effectively unusable for that pool. The pool admin is therefore incentivized to allowlist the router, unknowingly opening the bypass to all users. No special privileges, flash loans, or unusual token behavior are required — a single call to `MetricOmmSimpleRouter.exactInputSingle` suffices.

---

### Recommendation

The `SwapAllowlistExtension` should gate on the actual end user, not the immediate caller of `pool.swap`. Two options:

1. **Check `recipient` instead of `sender`**: The router always sets `recipient` to the user's address. Changing the check to `allowedSwapper[msg.sender][recipient]` would correctly identify the economic beneficiary. However, `recipient` can be set to any address, so this is only safe if the intent is to gate by who receives the output.

2. **Preferred — pass the originating user explicitly**: Add a user-identity field to `extensionData` that the router populates with `msg.sender` before calling the pool, and have the extension decode and verify it. This requires a coordinated change to the router and extension.

The simplest safe fix consistent with the existing design is to gate on `recipient` (the address that receives the output token), since the router always sets this to the actual end user:

```solidity
function beforeSwap(address, address recipient, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin allowlists `alice` directly: `setAllowedToSwap(pool, alice, true)`.
3. Admin allowlists the router so legitimate users can use it: `setAllowedToSwap(pool, address(router), true)`.
4. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. The router calls `pool.swap(recipient=bob, ...)`. The pool's `msg.sender` is the router.
6. `_beforeSwap(sender=router, ...)` is called; the extension checks `allowedSwapper[pool][router] == true` → passes.
7. `bob` successfully swaps on the curated pool despite never being individually allowlisted.

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
