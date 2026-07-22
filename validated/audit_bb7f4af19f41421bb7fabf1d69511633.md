### Title
`DepositAllowlistExtension` gates on LP position `owner` instead of actual depositor `sender`, allowing any address to bypass the deposit allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook receives two address parameters: `sender` (the actual caller of `addLiquidity`, i.e., the address providing tokens) and `owner` (the LP position recipient). The implementation silently discards `sender` and checks `owner` against the allowlist instead. Any unprivileged address can therefore deposit tokens into a restricted pool by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls `_beforeAddLiquidity(msg.sender, owner, ...)`, which encodes both arguments and forwards them to the extension: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` passes `sender` as the first argument and `owner` as the second: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` ignores the first argument (`sender`) entirely and checks only `owner`: [3](#0-2) 

The mapping is named `allowedDepositor` and the NatDoc says "Gates `addLiquidity` by depositor address", confirming the intent is to restrict the token-providing caller, not the LP position recipient: [4](#0-3) 

**Attack path (single transaction):**

1. Pool is configured with `DepositAllowlistExtension`; only `Alice` is in `allowedDepositor[pool]`.
2. `Bob` (not allowlisted) calls `pool.addLiquidity(owner = Alice, salt, deltas, callbackData, extensionData)`.
3. The extension evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
4. The pool executes the liquidity callback against `Bob` (`msg.sender`), pulling Bob's tokens.
5. The LP position (shares) is credited to `Alice`; Bob's tokens are now inside the pool.

Bob loses his tokens; Alice receives an unsolicited LP position; the pool has accepted a deposit from a non-allowlisted address. Because `removeLiquidity` enforces `msg.sender == owner`, Alice (not Bob) controls the position.

---

### Impact Explanation

The deposit allowlist — an admin-configured guard — is fully bypassed by any unprivileged address. The pool receives tokens from non-allowlisted sources, violating the invariant the pool admin intended to enforce. Depending on the deployment context (e.g., regulatory-gated pools, curated liquidity programs), this constitutes a broken core pool access-control flow. The depositor suffers direct loss of their own principal (tokens transferred in, LP position credited to a third party they do not control).

---

### Likelihood Explanation

The bypass requires no special privilege, no flash loan, and no multi-step setup. Any EOA or contract can execute it in a single transaction by supplying any allowlisted address as `owner`. The allowlist is publicly readable on-chain, so the set of valid bypass targets is trivially enumerable.

---

### Recommendation

Replace the ignored first parameter with a named `sender` variable and check it instead of `owner`:

```solidity
// Before (buggy):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}

// After (fixed):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Assume pool is deployed with DepositAllowlistExtension.
// Alice is allowlisted; Bob is not.

contract BypassDepositAllowlist {
    IMetricOmmPool pool;
    IERC20 token0;
    IERC20 token1;
    address alice;

    function exploit() external {
        // Bob calls addLiquidity naming Alice as owner.
        // The extension checks allowedDepositor[pool][alice] == true → passes.
        // Bob's tokens are pulled via the callback; Alice receives the LP shares.
        LiquidityDelta memory delta = /* construct valid delta */;
        pool.addLiquidity(
            alice,          // owner — allowlisted, passes the guard
            0,              // salt
            delta,
            abi.encode(token0, token1),  // callbackData
            ""              // extensionData
        );
        // Bob has lost his tokens; Alice holds the LP position Bob cannot withdraw.
    }

    // Pool calls back here to pull tokens from Bob (msg.sender of addLiquidity).
    function metricOmmAddLiquidityCallback(uint256 amount0, uint256 amount1, bytes calldata) external {
        token0.transfer(msg.sender, amount0);
        token1.transfer(msg.sender, amount1);
    }
}
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-14)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
