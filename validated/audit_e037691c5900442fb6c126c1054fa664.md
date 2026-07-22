Looking at the code gathered, I need to trace the swap allowlist guard path through the router to determine if a bypass exists.

From `SwapAllowlistExtension.sol` (line 31–41), the `beforeSwap` hook checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = the pool, and `sender` = the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router calls `pool.swap()`, so `sender = router_address`, not the end user.

From `ExtensionCalling.sol` (lines 151–176), the pool passes `sender` (its own `msg.sender`) verbatim into `_beforeSwap`, confirming the router address is what the extension sees.

The scan target explicitly flags this: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."*

This creates a forced dilemma for pool admins:
- **Don't allowlist the router** → allowlisted users cannot use `MetricOmmSimpleRouter` at all (their swaps revert because `allowedSwapper[pool][router] = false`)
- **Allowlist the router** → any user, allowlisted or not, can bypass the gate by routing through the public router

---

### Title
SwapAllowlistExtension gates the router address instead of the end user, enabling full allowlist bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` equals the router contract address, not the originating user. A pool admin who allowlists the router to permit allowlisted users to access the pool via the router simultaneously opens the pool to every unpermissioned user, completely defeating the allowlist guard.

### Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against `allowedSwapper[pool][sender]`. [1](#0-0) 

The pool's `_beforeSwap` dispatcher in `ExtensionCalling.sol` passes its own `msg.sender` (the direct caller of `pool.swap()`) as the `sender` argument forwarded to every extension. [2](#0-1) 

`MetricOmmSimpleRouter` is a public periphery contract. When any user calls the router, the router calls `pool.swap(...)`, making `msg.sender` inside the pool equal to the router address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. [3](#0-2) 

The pool admin faces an impossible choice:

| Admin action | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Do **not** allowlist router | Cannot use router (reverts) | Correctly blocked |
| **Allowlist router** | Can use router | **Also bypass — anyone can swap** |

There is no configuration that allows only allowlisted users to use the router while blocking non-allowlisted users.

### Impact Explanation
Any user can bypass a pool's swap allowlist by calling `MetricOmmSimpleRouter` whenever the pool admin has allowlisted the router. Non-allowlisted users gain full swap access to a restricted pool, allowing them to execute swaps against LP liquidity at oracle-derived prices. LP providers suffer direct loss of principal because their liquidity is consumed by actors the pool was explicitly configured to exclude. This breaks the core allowlist invariant and constitutes a direct loss of LP assets above Sherlock thresholds.

### Likelihood Explanation
The likelihood is medium-high. Pool admins who deploy a `SwapAllowlistExtension`-gated pool and also want their allowlisted users to benefit from router slippage protection and multi-hop routing will naturally allowlist the router. The router is a public, production periphery contract. The misconfiguration is not obvious from the extension's interface or documentation, and the admin has no way to achieve the intended behavior (router access for allowlisted users only) without the bypass. [4](#0-3) 

### Recommendation
The `beforeSwap` hook should gate on the economically relevant actor. Two options:

1. **Pass the originating user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router convention.
2. **Check `recipient` instead of `sender`**: If the pool's design guarantees that the recipient is the beneficiary of the swap, gate on `recipient`. This is only correct if the router always sets `recipient = original_caller`.
3. **Document that the router must never be allowlisted**: Add an explicit guard in `setAllowedToSwap` that rejects the router address, forcing admins to use direct pool calls for allowlisted access.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (BEFORE_SWAP_ORDER = extension 1)
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker address

Attack:
  - attacker (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle(pool, zeroForOne, amount, priceLimit, extensionData)
  - Router calls pool.swap(recipient=attacker, ...)
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks: allowedSwapper[pool][router] == true  → passes
  - Swap executes; attacker receives output tokens from LP liquidity

Result:
  - Non-allowlisted attacker successfully swaps in a restricted pool
  - LP funds are consumed by an actor the allowlist was designed to exclude
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L151-176)
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
```
