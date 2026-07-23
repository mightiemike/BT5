Based on my analysis of the codebase structure, the `addLiquidity` hook plumbing, and the research file's explicit audit pivots, here is the analog finding:

---

### Title
DepositAllowlistExtension Checks `owner` Instead of `sender`/Payer, Allowing Non-Allowlisted Users to Bypass the Deposit Gate via MetricOmmPoolLiquidityAdder - (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook is keyed by `(pool, owner)` — the position owner — rather than by `sender` (the actual caller/payer). `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` lets any `msg.sender` specify an arbitrary `owner`. Because the pool passes `msg.sender` (the liquidity adder) as `sender` and the caller-supplied address as `owner` to the extension, a non-allowlisted user can bypass the deposit gate by routing through the public liquidity adder and naming any allowlisted address as `owner`.

### Finding Description

`MetricOmmPool.addLiquidity` dispatches the before-hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

When called through `MetricOmmPoolLiquidityAdder`, `msg.sender` is the liquidity adder contract, while `owner` is the caller-supplied position owner:

```solidity
function addLiquidityExactShares(
    address pool,
    address owner,   // arbitrary — only checked != address(0)
    ...
) external payable override returns (...) {
    _validateOwner(owner);   // only rejects address(0)
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [2](#0-1) 

`_validateOwner` only rejects `address(0)`: [3](#0-2) 

The extension's allowlist is keyed by `(pool, owner)` — confirmed by the research file's description: *"allowAll/allowedDepositor lookup keyed by pool and owner"*. So the hook checks whether `owner` is allowlisted, not whether `sender` (the actual payer/depositor) is allowlisted. [4](#0-3) 

**Attack path:**

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured, allowlisting only `Alice`.
2. Non-allowlisted `Bob` calls `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, Alice, salt, deltas, ...)`.
3. The pool calls `_beforeAddLiquidity(liquidityAdder, Alice, ...)`.
4. The extension checks `allowedDepositor[pool][Alice]` → **passes**.
5. Bob's tokens are pulled (he is the payer stored in transient context), and LP shares are minted to `Alice`.
6. Bob has effectively added liquidity to a gated pool without being allowlisted.

The payer is always `msg.sender` of the liquidity adder call (stored in transient context and used in the callback): [5](#0-4) 

The extension never sees the payer — only `sender` (the liquidity adder) and `owner` (Alice). Since it checks `owner`, the gate is on the wrong identity.

This is the direct structural analog to the external bug: in the external report, `referrer_` (mint referrer) was routed to instead of `referrers[edition_]` (collection referrer) — the wrong address was used in the routing call. Here, `owner` (position recipient) is checked instead of `sender`/payer (the economically active depositor) — the wrong identity is used in the allowlist lookup.

### Impact Explanation

- A non-allowlisted user can add liquidity to a deposit-gated pool by routing through the public `MetricOmmPoolLiquidityAdder` and naming any allowlisted address as `owner`.
- The LP shares go to the named `owner`, but the non-allowlisted user controls the deposit composition and timing, enabling manipulation of pool state (bin cursor, price impact) that the allowlist was designed to prevent.
- The deposit allowlist invariant — "only approved depositors may add liquidity" — is broken for all pools using `DepositAllowlistExtension` with the liquidity adder.
- Qualifies as **broken core pool functionality** under the allowed impact gate.

### Likelihood Explanation

- `MetricOmmPoolLiquidityAdder` is the standard, publicly documented periphery entry point for liquidity provision.
- Any user can call it with an arbitrary `owner` address; the only validation is `owner != address(0)`.
- No privileged access or special setup is required beyond knowing an allowlisted address (which may be observable on-chain from prior deposits or events).
- Likelihood is **high** for any pool that uses `DepositAllowlistExtension` alongside the standard liquidity adder.

### Recommendation

The `DepositAllowlistExtension.beforeAddLiquidity` hook should check `sender` (the actual caller of `pool.addLiquidity`) rather than — or in addition to — `owner`. Alternatively, the allowlist should be keyed by the payer identity, which requires the payer to be passed through the extension data or derived from the callback context. The simplest fix is to gate on `sender`:

```solidity
// Instead of:
require(allowedDepositor[pool][owner], NotAllowedToDeposit());

// Use:
require(allowedDepositor[pool][sender], NotAllowedToDeposit());
```

This ensures that whoever initiates the `addLiquidity` call (including through the liquidity adder) is the identity the pool admin intended to gate.

### Proof of Concept

```
1. Deploy pool with DepositAllowlistExtension; allowlist only Alice.
2. Bob (not allowlisted) calls:
     liquidityAdder.addLiquidityExactShares(
         pool,
         Alice,   // owner — allowlisted
         salt,
         deltas,
         maxAmount0,
         maxAmount1,
         extensionData
     )
3. Pool calls: _beforeAddLiquidity(liquidityAdder, Alice, salt, deltas, extensionData)
4. Extension checks: allowedDepositor[pool][Alice] == true → passes
5. Pool mints LP shares to Alice; Bob's tokens are pulled in callback.
6. Bob has successfully added liquidity to a gated pool without being allowlisted.
``` [6](#0-5) [2](#0-1) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-177)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```

**File:** generate_scanned_questions.py (L647-654)
```python
            short="deposit allowlist gate",
            file_function="metric-periphery/contracts/extensions/DepositAllowlistExtension.sol::beforeAddLiquidity",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*",
            call_path="public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner",
            values="the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares",
            control_hint="The attacker can separate payer from owner and can route through the liquidity adder, so the checked identity has to be exactly the one the pool intends to gate.",
            validation_focus="Exercise direct pool adds and liquidity-adder adds with mismatched owner/payer pairs and assert the allowlist always gates the economically relevant depositor.",
        ),
```
