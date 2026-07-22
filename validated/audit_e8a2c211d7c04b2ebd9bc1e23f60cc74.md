Looking at the swap allowlist path, I need to trace how `sender` flows from the public router through the pool into the extension check. [1](#0-0) [2](#0-1) [3](#0-2) 

The pool passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it verbatim to the extension. When the router calls `pool.swap()`, `msg.sender` = router, so the extension sees `sender` = router, not the actual end-user.

---

### Title
SwapAllowlistExtension gates on the immediate pool caller (router address) instead of the actual end-user, allowing any user to bypass the per-pool swap allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which equals `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address. If the pool admin allowlists the router to enable router-mediated swaps for allowlisted users, any unprivileged user can bypass the individual allowlist by routing through the router.

### Finding Description
In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- immediate caller, not the end-user
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Where `msg.sender` = pool and `sender` = the immediate caller of `pool.swap()`.

**Direct call path:** user → `pool.swap()` → `sender` = user → allowlist checks user ✓  
**Router path:** user → `router.exactInputSingle()` → router → `pool.swap()` → `sender` = router → allowlist checks router

If the pool admin allowlists the router (`setAllowedToSwap(pool, router, true)`) to enable router-mediated swaps for their allowlisted users, the check passes for **any** user who routes through the router, regardless of individual allowlist status.

### Impact Explanation
Any user not on the allowlist can bypass the swap restriction by routing through `MetricOmmSimpleRouter`. This breaks the pool admin's access control invariant. In pools designed to restrict swaps to specific institutional or KYC-verified participants, unauthorized users can interact with the pool and drain LP liquidity. The allowlist is the sole mechanism preventing unauthorized swaps; once bypassed, there is no secondary guard.

### Likelihood Explanation
Medium. The pool admin must have allowlisted the router for this bypass to work. However, allowlisting the router is a natural and expected configuration for any pool that wants to support router-mediated swaps for its allowlisted users — the admin cannot simultaneously allow allowlisted users to use the router and block non-allowlisted users from doing the same. The attacker requires no special privileges, only access to the public router.

### Recommendation
The extension must check the actual end-user's address, not the immediate pool caller. Two options:

1. **Trusted-forwarder pattern:** The router appends the original `msg.sender` to `extensionData`; the extension reads and verifies it against the allowlist (requires router to be a trusted forwarder recognized by the extension).
2. **Pool-level sender tracking:** The pool exposes the original initiator via a transient storage slot that the extension reads directly, bypassing the `sender` parameter entirely.

### Proof of Concept
1. Pool admin deploys pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router so Alice can use it: `setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `router.exactInputSingle(pool=pool, ...)`.
5. Router calls `pool.swap()` → pool's `msg.sender` = router.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes against the restricted pool, bypassing the allowlist entirely. [1](#0-0) [3](#0-2) [4](#0-3)

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
