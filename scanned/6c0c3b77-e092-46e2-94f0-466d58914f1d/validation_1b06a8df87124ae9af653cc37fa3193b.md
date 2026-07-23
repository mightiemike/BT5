### Title
SwapAllowlistExtension Gates on Router Address Instead of User Address, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` looks up the allowlist using `sender`, which the pool sets to `msg.sender` at the pool call boundary. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router address to support router-mediated swaps, every user — including those not on the allowlist — can bypass the guard by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then performs the allowlist lookup keyed on `sender`: [3](#0-2) 

The effective check is `allowedSwapper[pool][sender]`. When the call originates from `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address. The pool admin must therefore choose between two broken configurations:

| Configuration | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert, even for allowlisted users (DoS) |
| Router **allowlisted** | Every user bypasses the allowlist by routing through the router |

The second case is the fund-impacting path. A pool admin who wants to support both direct and router-mediated swaps for a restricted set of users has no correct configuration: allowlisting the router opens the gate to everyone.

The structural analog to the external report is exact: the external bug keys a reward mapping on a freshly-deployed token address (overwriting the prior entry), so the wrong token is consulted for every subsequent balance lookup. Here, the allowlist mapping is keyed on the router address instead of the user address, so the wrong identity is consulted for every router-mediated swap — the guard reads a value that was never intended to authorize the actual caller.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., KYC-gated, institutional, or whitelist-only pools) can be accessed by any unprivileged user via the public router. Unrestricted access exposes LP positions to toxic flow, adverse selection, and volume-driven fee extraction that the allowlist was designed to prevent, resulting in direct loss of LP principal over time.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it. The only precondition is that the pool admin has allowlisted the router — a natural and expected operational step for any pool that intends to support the standard periphery swap path. No privileged access, no malicious setup, and no non-standard tokens are required.

---

### Recommendation

The extension must check the identity of the **end user**, not the intermediary. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes the real user address into `extensionData`; the extension decodes and checks it. This requires a trusted router or a signed payload.
2. **Check `recipient` instead of `sender`**: If the pool's swap semantics guarantee that `recipient` is always the end user, the allowlist can gate on `recipient`. However, `recipient` is also caller-controlled, so this must be validated against the intended trust model.
3. **Separate router-aware allowlist logic**: Introduce a router registry in the extension so that when `sender` is a known router, the extension extracts and checks the user address from `extensionData` rather than `sender` directly.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin allowlists only `trustedUser`:
       extension.setAllowedToSwap(pool, trustedUser, true)
3. Pool admin also allowlists the router so trustedUser can use it:
       extension.setAllowedToSwap(pool, router, true)
4. Attacker (not on allowlist) calls:
       router.exactInput(pool, tokenIn, amountIn, ...)
5. Pool calls _beforeSwap(msg.sender=router, ...)
6. Extension checks: allowedSwapper[pool][router] == true  → passes
7. Attacker's swap executes in the restricted pool.
```

The allowlist check at step 6 reads `allowedSwapper[pool][router]` — the router's entry — instead of `allowedSwapper[pool][attacker]`. Because the pool admin was forced to allowlist the router to support `trustedUser`'s router path, the mapping entry for the router is `true`, and the guard is fully bypassed for every caller. [4](#0-3) [5](#0-4)

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
