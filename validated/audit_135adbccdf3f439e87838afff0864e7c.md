### Title
`SLOW_MODE_FEE` Compared Against External Token Amount Without Decimal Scaling — (`core/contracts/EndpointTx.sol`)

### Summary

In `EndpointTx.submitSlowModeTransactionImpl`, the `DepositInsurance` minimum-amount guard compares `txn.amount` (an external ERC-20 token quantity) directly against the constant `SLOW_MODE_FEE = 1_000_000`, which is denominated in 6-decimal precision. No scaling is applied for the actual quote token's decimals. The protocol already exposes `Clearinghouse.getSlowModeFee()`, which correctly scales `SLOW_MODE_FEE` by `10^(decimals − 6)`, proving the developer intended decimal-aware handling — but the on-chain guard omits it.

### Finding Description

`SLOW_MODE_FEE` is defined as `1_000_000` with the comment `// $1`, implying it represents one dollar in 6-decimal (USDC-style) precision. [1](#0-0) 

`Clearinghouse.getSlowModeFee()` correctly scales this constant to the actual quote token's external precision: [2](#0-1) 

However, in `submitSlowModeTransactionImpl`, the `DepositInsurance` branch checks `txn.amount` — a raw ERC-20 external amount — directly against the unscaled `SLOW_MODE_FEE`: [3](#0-2) 

`txn.amount` is the raw token quantity passed by the caller and transferred via `handleDepositTransfer`. For a quote token with 18 decimals, `SLOW_MODE_FEE = 1e6` in external units equals `0.000000000001` tokens — effectively zero. For a token with fewer than 6 decimals, `getSlowModeFee()` itself would underflow (`uint8` subtraction), and the guard would demand far more than $1.

The `submitSlowModeTransaction` entry point is `external` with no access control, so any unprivileged caller can reach this path. [4](#0-3) 

### Impact Explanation

For an 18-decimal quote token, the minimum-deposit invariant for `DepositInsurance` is broken: any caller can queue an insurance deposit of `1e6` wei (≈ $0.000000000001), bypassing the intended $1 floor. The insurance accounting in `Clearinghouse.depositInsurance` scales the amount correctly to X18 precision, so the on-chain state records a near-zero insurance credit while the guard is satisfied. The protection against dust-deposit spam is entirely ineffective for non-6-decimal tokens. [5](#0-4) 

For tokens with fewer than 6 decimals, `getSlowModeFee()` underflows, and the unscaled guard would demand an amount far exceeding $1, making legitimate insurance deposits impossible.

### Likelihood Explanation

The Nado protocol is designed to support multiple collateral tokens with varying decimals (`MAX_DECIMALS = 18`, and the deposit path explicitly normalises decimals). Any deployment where the quote token is not exactly 6 decimals triggers the inconsistency. The entry point is publicly callable with no privilege requirement. [6](#0-5) 

### Recommendation

Replace the raw `SLOW_MODE_FEE` comparison with a decimal-scaled value, consistent with `getSlowModeFee()`:

```solidity
IERC20Base quote = _getQuote();
uint8 decimals = quote.decimals();
uint128 scaledFee = uint128(SLOW_MODE_FEE) * uint128(10 ** (decimals - 6));
require(txn.amount >= scaledFee, ERR_DEPOSIT_TOO_SMALL);
```

Alternatively, call `clearinghouse.getSlowModeFee()` directly and compare against its return value.

### Proof of Concept

1. Deploy Nado with an 18-decimal quote token (e.g., a WETH-denominated clearinghouse).
2. Construct a `DepositInsurance` transaction with `amount = 1_000_000` (1e6 wei).
3. Call `Endpoint.submitSlowModeTransaction(encodedTx)` from any EOA.
4. The check `require(txn.amount >= uint128(SLOW_MODE_FEE))` passes because `1_000_000 >= 1_000_000`.
5. `handleDepositTransfer` pulls `1e6` wei (≈ $0) from the caller.
6. After the 3-day delay, `processSlowModeTransactionImpl` credits `insurance += 1e6 * 1 = 1e6` (X18 units ≈ $0.000000000001).
7. The intended $1 minimum is completely bypassed. [7](#0-6)

### Citations

**File:** core/contracts/common/Constants.sol (L19-19)
```text
uint8 constant MAX_DECIMALS = 18;
```

**File:** core/contracts/common/Constants.sol (L23-23)
```text
int128 constant SLOW_MODE_FEE = 1000000; // $1
```

**File:** core/contracts/Clearinghouse.sol (L252-267)
```text
    function depositInsurance(bytes calldata transaction)
        external
        virtual
        onlyEndpoint
    {
        IEndpoint.DepositInsurance memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.DepositInsurance)
        );
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int256 multiplier = int256(
            10**(MAX_DECIMALS - _decimals(QUOTE_PRODUCT_ID))
        );
        int128 amount = int128(txn.amount) * int128(multiplier);
        insurance += amount;
    }
```

**File:** core/contracts/Clearinghouse.sol (L759-766)
```text
    function getSlowModeFee() external view returns (uint256) {
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(
            spotEngine.getConfig(QUOTE_PRODUCT_ID).token
        );
        int256 multiplier = int256(10**(token.decimals() - 6));
        return uint256(int256(SLOW_MODE_FEE) * multiplier);
    }
```

**File:** core/contracts/EndpointTx.sol (L332-385)
```text
    function submitSlowModeTransactionImpl(bytes calldata transaction) public {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );

        // special case for DepositCollateral because upon
        // slow mode submission we must take custody of the
        // actual funds

        address sender = msg.sender;

        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            revert();
        } else if (txType == IEndpoint.TransactionType.DepositInsurance) {
            IEndpoint.DepositInsurance memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositInsurance)
            );
            require(
                txn.amount >= uint128(SLOW_MODE_FEE),
                ERR_DEPOSIT_TOO_SMALL
            );
            handleDepositTransfer(_getQuote(), sender, uint256(txn.amount));
        } else if (
            txType == IEndpoint.TransactionType.WithdrawInsurance ||
            txType == IEndpoint.TransactionType.DelistProduct ||
            txType == IEndpoint.TransactionType.DumpFees ||
            txType == IEndpoint.TransactionType.RebalanceXWithdraw ||
            txType == IEndpoint.TransactionType.UpdateTierFeeRates ||
            txType == IEndpoint.TransactionType.AddNlpPool ||
            txType == IEndpoint.TransactionType.UpdateNlpPool ||
            txType == IEndpoint.TransactionType.DeleteNlpPool ||
            txType == IEndpoint.TransactionType.ForceRebalanceNlpPool ||
            txType == IEndpoint.TransactionType.NlpProfitShare ||
            txType == IEndpoint.TransactionType.UpdateBuilder
        ) {
            require(sender == owner());
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }

        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
        // TODO: to save on costs we could potentially just emit something
        // for now, we can just create a separate loop in the engine that queries the remote
        // sequencer for slow mode transactions, and ignore the possibility of a reorgy attack
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/Endpoint.sol (L173-183)
```text
    function submitSlowModeTransaction(bytes calldata transaction)
        external
        virtual
    {
        _delegatecallEndpointTx(
            abi.encodeWithSelector(
                EndpointTx.submitSlowModeTransactionImpl.selector,
                transaction
            )
        );
    }
```
