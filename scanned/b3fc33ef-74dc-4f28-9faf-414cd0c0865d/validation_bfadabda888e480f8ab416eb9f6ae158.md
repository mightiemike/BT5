### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address. A pool admin who allowlists the router to enable router-mediated swaps for their permitted users inadvertently opens the pool to every user, completely defeating the allowlist guard.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → MetricOmmPool.swap(recipient, ...) [msg.sender = router]
     → ExtensionCalling._beforeSwap(sender = msg.sender = router, ...)
     → CallExtension.callExtension(extension, abi.encodeCall(beforeSwap, (router, ...)))
     → SwapAllowlistExtension.beforeSwap(sender = router, ...)
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed on that `sender`: [3](#0-2) 

When the call originates from `MetricOmmSimpleRouter`, `sender` is the router's address. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable dilemma for any pool admin who deploys a restricted pool:

| Admin choice | Consequence |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user on the network can bypass the allowlist by calling through the router |

There is no configuration that simultaneously permits router-mediated swaps for approved users and blocks unapproved users.

`CallExtension.callExtension` performs no gas capping and correctly propagates reverts, so the guard itself executes — but it executes against the wrong identity: [4](#0-3) 

Additionally, `SwapAllowlistExtension.beforeSwap` is declared without the `onlyPool` modifier that `BaseMetricExtension` applies to its default stubs, meaning the function is callable by any address. While this alone does not create a bypass (non-pool callers have no allowlist entries and always revert), it removes the factory-registry identity check that the base contract was designed to enforce: [5](#0-4) [3](#0-2) 

---

### Impact Explanation

Any unprivileged user can swap in a pool that the admin intended to restrict to a specific set of counterparties by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The allowlist guard is silently bypassed: the extension executes, returns the correct selector, and the swap proceeds. LP providers in a restricted pool (e.g., an institutional or KYC-gated pool) are exposed to counterparties they explicitly excluded, which can result in adverse-selection losses and violation of the pool's access-control invariant.

---

### Likelihood Explanation

The bypass requires no special privilege, no malicious setup, and no non-standard token behavior. Any user with a token balance can call the public router. The router is a canonical, documented entry point that users are expected to use. The likelihood that a pool admin allowlists the router (to give their approved users a better UX) is high, making the bypass reachable in normal operation.

---

### Recommendation

The extension must check the identity of the **economic actor**, not the immediate `msg.sender` of `pool.swap()`. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `abi.encode(msg.sender)` into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to populate the field honestly, which is acceptable given it is a protocol-controlled contract.

2. **Check `sender` only for direct pool calls; require the router to forward the user address as `sender`**: Modify the router so it passes the original caller as the `sender` argument to `pool.swap` (requires a pool-level change to accept a caller-specified sender, which has its own trust implications).

Additionally, add the `onlyPool` modifier to `SwapAllowlistExtension.beforeSwap` (and `DepositAllowlistExtension.beforeAddLiquidity`) to match the defensive posture of `BaseMetricExtension`:

```solidity
function beforeSwap(...) external view override onlyPool returns (bytes4) { ... }
```

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Admin allowlists the router so approved users can swap via the router.
// allowedSwapper[pool][router] = true

// Attacker (not in allowlist) calls:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: restrictedPool,
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Router calls pool.swap(msg.sender=router, ...)
// Extension checks allowedSwapper[pool][router] == true → passes
// Attacker receives token1 output despite not being allowlisted.
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

**File:** metric-core/contracts/libraries/CallExtension.sol (L8-32)
```text
  function callExtension(address extension, bytes memory data) internal {
    (bool success, bytes memory result) = extension.call(data);
    if (!success) {
      if (result.length > 0) {
        assembly ("memory-safe") {
          revert(add(result, 32), mload(result))
        }
      }
      revert ExtensionCallFailed();
    }
    if (result.length < 32) {
      revert InvalidExtensionResponse();
    }
    bytes4 returnedSelector;
    assembly ("memory-safe") {
      returnedSelector := mload(add(result, 32))
    }
    bytes4 expectedSelector;
    assembly ("memory-safe") {
      expectedSelector := mload(add(data, 32))
    }
    if (returnedSelector != expectedSelector) {
      revert InvalidExtensionResponse();
    }
  }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
