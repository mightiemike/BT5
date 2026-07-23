### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End-User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router address, not the end-user. If the router is allowlisted (the only way allowlisted users can trade through it), the allowlist becomes a no-op: any unprivileged user can bypass it by routing through the same public contract.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded from the pool — i.e., the router address when the trade enters through `MetricOmmSimpleRouter`: [3](#0-2) 

The allowlist is keyed `pool → swapper`. When the router is the immediate caller, the checked identity is the router, not the end-user. This creates an inescapable dilemma for any curated pool:

- **If the router is allowlisted** (required for any allowlisted user to trade through it): every user — allowlisted or not — can bypass the gate by routing through the same public contract.
- **If the router is not allowlisted**: allowlisted users cannot use the router at all, breaking the supported periphery path.

The analog to the static cipher key is exact: the router address is a fixed, publicly known value. Allowlisting it to enable legitimate users simultaneously hands every unprivileged user a master key, just as the hardcoded `r3v4` key allowed anyone with source-code access to decrypt the wallet password.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses provides no real restriction. Any unprivileged user can call `MetricOmmSimpleRouter.exactInput/exactOutput` targeting the pool and the allowlist check passes against the router's allowlisted address. The user receives output tokens from the pool as if they were allowlisted. This is a direct, fund-impacting policy bypass: the pool admin's curation intent is completely defeated, and the pool's LP assets are exposed to unrestricted trading.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any user who can read the deployment address (which is public) can route through it. No privileged access, no special setup, and no malicious token behavior is required. The only precondition is that the pool admin has allowlisted the router — a step that is operationally necessary for any allowlisted user to trade through the supported periphery path.

---

### Recommendation

The extension must receive and check the **ultimate end-user's identity**, not the immediate pool caller. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes the actual `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the pool to enforce that the router cannot forge this value (e.g., via a trusted-forwarder pattern or signed context).

2. **Check `sender` at the router level before calling the pool**: The router reads `isAllowedToSwap(pool, msg.sender)` from the extension and reverts before forwarding to the pool. This keeps the check at the periphery boundary where the real user identity is still available.

Either way, the extension's `beforeSwap` as currently written cannot distinguish end-users from routers and must not be used as a per-user gate on pools that support router-mediated swaps.

---

### Proof of Concept

```
// Setup: curated pool with SwapAllowlistExtension; only `alice` is allowlisted.
// Pool admin allowlists the router so alice can trade through it.
extension.setAllowedToSwap(pool, address(router), true);
extension.setAllowedToSwap(pool, alice, true);

// Attack: bob (not allowlisted) routes through the public router.
// pool.swap() is called with msg.sender = router.
// Extension checks allowedSwapper[pool][router] → true → passes.
// Bob receives output tokens as if he were allowlisted.
vm.prank(bob);
router.exactInput(ExactInputParams({
    pool: pool,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp,
    extensionData: ""
}));
// Bob's swap succeeds; the allowlist is bypassed.
``` [4](#0-3) [5](#0-4)

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
