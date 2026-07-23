### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the originating user. If the pool admin allowlists the router (which is required for any allowlisted user to use the router), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes this value directly into the hook calldata:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` = pool):

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

When a user calls `MetricOmmSimpleRouter.exactInput()` (or any router entry point), the router calls `pool.swap()`, so `sender` = router address. The pool admin faces an inescapable dilemma:

| Configuration | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert — allowlisted users cannot use the router |
| Router **allowlisted** | Every user, allowlisted or not, can bypass the gate by routing through the router |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users. The `extensionData` field is available but `SwapAllowlistExtension` ignores it entirely, so the router cannot pass the originating user's address through any supported channel.

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., restricted to KYC'd counterparties, institutional market makers, or whitelisted strategies) and allowlists the router to give those users access to the periphery inadvertently opens the pool to every public user. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInput()` targeting the restricted pool and the allowlist check passes because the router is allowlisted. This constitutes a complete bypass of the admin-configured access boundary, allowing unauthorized swaps that drain LP assets at oracle-quoted prices the pool was not intended to serve to arbitrary counterparties.

---

### Likelihood Explanation

The bypass is reachable by any unprivileged user with no special setup beyond calling the public router. The only precondition is that the pool admin has allowlisted the router — a step they must take if any allowlisted user is expected to use the router. Pools that are configured with `SwapAllowlistExtension` and rely on the router for normal operation are fully exposed. The trigger is a single public call to `MetricOmmSimpleRouter`.

---

### Recommendation

The `SwapAllowlistExtension` should gate on the **originating user**, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass originating user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.
2. **Check `sender` and fall back to `extensionData`**: If `sender` is a known router, extract the real user from `extensionData` and check that address instead.
3. **Separate router-level allowlist**: Deploy a router wrapper that enforces the allowlist before calling the pool, and restrict the pool to only accept calls from that wrapper.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // must do this for router to work
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInput(...)
    → router calls pool.swap(recipient, ...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
    → swap executes for attacker despite not being allowlisted

Result:
  - attacker swaps on a pool intended to be restricted
  - allowlist invariant broken; LP assets exposed to unauthorized counterparty
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
