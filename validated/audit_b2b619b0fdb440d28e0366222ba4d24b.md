### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Any User Can Swap on Allowlisted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument the pool forwards, which is `msg.sender` of the `pool.swap()` call — the router address, not the end user. When a pool admin allowlists `MetricOmmSimpleRouter` to enable router-mediated swaps, every user who routes through that public, permissionless contract passes the allowlist check, regardless of whether they are individually permitted.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct key for the per-pool mapping) and `sender` is the address the pool forwards as the caller. `ExtensionCalling._beforeSwap` passes whatever `sender` value the pool supplies directly into the encoded call:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [2](#0-1) 

The pool derives `sender` from `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` calls `pool.swap()`, `msg.sender` inside the pool is the router contract, so the extension receives the router address as `sender` — not the end user.

The test suite confirms this binding: the allowlist is set for `callers[0]` (the `TestCaller` wrapper, i.e., the intermediate contract), not for `users[0]` (the human address):

```solidity
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
_swap(0, users[0], false, int128(1000), type(uint128).max);
``` [3](#0-2) 

`MetricOmmSimpleRouter` is a public, permissionless contract — there is no access control on who may call it. A pool admin who wants router-mediated swaps to work on an allowlisted pool must add the router address to `allowedSwapper`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` evaluates to `true` for every caller, because the extension never inspects the actual end user.

---

### Impact Explanation

Any user — including those explicitly excluded from the allowlist — can bypass the swap gate on a curated pool by routing through `MetricOmmSimpleRouter`. The allowlist's purpose (e.g., KYC enforcement, institutional-only pools, regulatory compliance) is entirely defeated. The attacker receives real swap output tokens from a pool they are not permitted to trade on. This is a direct, fund-impacting policy bypass on every pool that combines `SwapAllowlistExtension` with router allowlisting.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary user-facing entry point described in the protocol documentation.
- A pool admin who deploys an allowlisted pool and wants users to be able to use the standard router will naturally allowlist the router address.
- The flaw is non-obvious: the admin believes they are enabling the router while still gating individual users, but the extension collapses both into a single router-level check.
- No privileged access, special tokens, or malicious setup is required. Any EOA can call the router.

---

### Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor — the end user — not the intermediate router. Two sound approaches:

1. **Pass the original caller through the pool**: Have `MetricOmmPool.swap()` accept an explicit `sender` parameter (verified against `msg.sender` or a trusted forwarder registry) so the router can forward the real user address. The extension then checks that address.

2. **Gate at the router level**: Add per-pool, per-user access control inside `MetricOmmSimpleRouter` itself, and do not allowlist the router in the extension. The extension then only needs to gate direct pool callers.

Do not use `tx.origin` as a substitute — it breaks contract-to-contract composability and introduces its own phishing risks.

---

### Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension (extension1 = SwapAllowlistExtension)
  extensionOrders.beforeSwap = extension slot 1 active
  allowedSwapper[pool][router] = true   ← admin allowlists router to enable router swaps
  allowedSwapper[pool][attacker] = false ← attacker is explicitly excluded

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      tokenIn: token0,
      tokenOut: token1,
      pool: pool,
      recipient: attacker,
      amountIn: X,
      ...
  })

  Router calls pool.swap(recipient=attacker, ...)
  Pool sets sender = msg.sender = address(router)
  ExtensionCalling._beforeSwap(sender=router, ...)
  SwapAllowlistExtension.beforeSwap:
      allowedSwapper[pool][router] == true  → check passes
  Swap executes; attacker receives token1 output.

Result:
  attacker, who is not in the allowlist, successfully swaps on a curated pool.
  The allowlist invariant is broken with no privileged action required.
``` [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L70-73)
```text
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
```
