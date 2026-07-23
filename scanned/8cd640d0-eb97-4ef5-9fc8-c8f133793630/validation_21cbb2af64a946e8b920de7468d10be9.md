### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Any Unprivileged User Can Swap on Allowlisted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool — which is the **immediate caller of `pool.swap()`**, not the ultimate economic actor. When a user routes through the public `MetricOmmSimpleRouter`, the router becomes `sender`. If the pool admin allowlists the router (the only way to support router-mediated swaps), every unprivileged user can bypass the per-user allowlist entirely by routing through the public periphery contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the pool calls the extension), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...)
    )
);
```

In `MetricOmmPool.swap`, `sender` is `msg.sender` of the pool call — i.e., whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`, so `sender` = **router address**, not the user's address.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → all router-mediated swaps revert for every user, breaking the primary user-facing entry point.
- **Allowlist the router** → `allowedSwapper[pool][router] = true`, so every call through the router passes the check regardless of who the ultimate user is.

There is no mechanism to simultaneously support router-mediated swaps and enforce per-user restrictions. Any non-allowlisted user can bypass the gate by calling `MetricOmmSimpleRouter` instead of `pool.swap()` directly.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict access (e.g., KYC-gated, institutional, or protocol-internal pools) loses its access control entirely once the router is allowlisted. Unauthorized users can execute swaps, drain LP-favorable pricing, or interact with pools that were designed to be closed to the public. This constitutes broken core pool functionality and a direct admin-boundary break: the allowlist policy the pool admin configured is rendered ineffective by a public periphery path.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the documented primary user-facing entry point for swaps. Pool admins who want their pool to be usable via the standard periphery must allowlist the router. The bypass requires no special privileges, no flash loans, and no complex setup — any user who calls the router instead of the pool directly bypasses the guard. Likelihood is **High**.

---

### Recommendation

The extension must gate the **ultimate economic actor**, not the immediate pool caller. Two viable approaches:

1. **Pass the original user address via `extensionData`**: The router encodes the real user address into `extensionData`; the extension decodes and checks it. This requires a trusted router or a signed proof.
2. **Check `recipient` instead of `sender`**: For swaps, the recipient is the address receiving output tokens and is harder to spoof than the intermediate caller. This is imperfect but narrows the attack surface.
3. **Document incompatibility**: If neither fix is adopted, the extension interface must clearly state that allowlisted pools must not allowlist any public router or multicall contract, and must only be accessed via direct `pool.swap()` calls.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, userA, true)   // only userA is allowed
3. Admin calls setAllowedToSwap(pool, router, true)  // router allowlisted for usability
4. userB (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           tokenIn: token0, tokenOut: token1,
           pool: pool, recipient: userB, ...
       })
5. Router calls pool.swap(...) — msg.sender to pool = router.
6. ExtensionCalling._beforeSwap forwards sender = router.
7. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
8. userB's swap executes on the supposedly restricted pool.
```

**Corrupted invariant**: `allowedSwapper[pool][userB] == false` but userB successfully swaps.
**Exact wrong value**: `sender` checked = `router` address; should be `userB` address. [1](#0-0) [2](#0-1)

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
