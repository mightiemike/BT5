### Title
SwapAllowlistExtension Allowlist Bypassed via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` intermediates a swap, `sender` becomes the router's address rather than the original user's address. If the router is allowlisted (the only way to permit router-mediated swaps on the pool), any non-allowlisted user can bypass the restriction by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the pool calls the extension), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which the pool sets to `msg.sender` of the `pool.swap()` call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
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

**Direct call path** (user → pool):
`sender` = user address → `allowedSwapper[pool][user]` is checked → correct.

**Router path** (user → router → pool):
`sender` = router address → `allowedSwapper[pool][router]` is checked → the original user's identity is never verified.

For any allowlisted user to use the router, the pool admin must allowlist the router address. But allowlisting the router means every user who calls the router passes the check, regardless of whether they are individually allowlisted. The pool admin cannot simultaneously:
- Allow allowlisted users to use the router (requires `allowedSwapper[pool][router] = true`), and
- Block non-allowlisted users from using the router (requires `allowedSwapper[pool][router] = false`).

This is structurally identical to the external bug: a guard that is correctly applied on one entry path (`claim()` / direct pool call) is silently absent on a parallel entry path (`claim_many()` / router-mediated call), with fund-impacting consequences.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, whitelisted market makers) can be bypassed by any unprivileged user who routes through `MetricOmmSimpleRouter`. The attacker receives output tokens from the pool at oracle-derived prices without being an authorized swapper. This breaks the core access-control invariant of the allowlist extension and constitutes unauthorized extraction of pool assets (token output) by an unprivileged actor.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract.
- Any user can call it with any pool address.
- The bypass requires only that the router is allowlisted on the target pool, which is the natural configuration for any pool that intends to support router-mediated swaps for its authorized users.
- No privileged access, special tokens, or malicious setup is required.

---

### Recommendation

The `SwapAllowlistExtension` must check the original user's identity, not the intermediary's. Two approaches:

1. **Pass original caller via `extensionData`**: Have the router encode `msg.sender` (the original user) into `extensionData` and have the extension decode and check it. The extension must also verify that `sender` is a trusted router before accepting the delegated identity.

2. **Check both `sender` and a router-forwarded origin**: Add a trusted-router registry to the extension. When `sender` is a trusted router, extract and check the original user from `extensionData`; otherwise check `sender` directly.

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata extensionData)
    external view override returns (bytes4)
{
    address pool = msg.sender;
    address effectiveSender = sender;
    if (trustedRouter[pool][sender] && extensionData.length >= 20) {
        effectiveSender = abi.decode(extensionData, (address));
    }
    if (!allowAllSwappers[pool] && !allowedSwapper[pool][effectiveSender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

---

### Proof of Concept

**Setup:**
- Pool P is deployed with `SwapAllowlistExtension`.
- Pool admin allowlists Alice: `allowedSwapper[P][Alice] = true`.
- Pool admin allowlists the router so Alice can use it: `allowedSwapper[P][router] = true`.

**Attack:**
1. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting pool P.
2. The router calls `P.swap(recipient=Bob, ...)`.
3. The pool calls `_beforeSwap(msg.sender=router, ...)`.
4. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[P][router]` → `true` → passes.
5. Bob receives output tokens from the pool at oracle price.

**Expected behavior:** Bob's swap should revert with `NotAllowedToSwap`.
**Actual behavior:** Bob's swap succeeds because the router's address passes the allowlist check instead of Bob's address. [1](#0-0) [2](#0-1) [3](#0-2)

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
