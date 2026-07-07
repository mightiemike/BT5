### Title
Missing Event Emission on `LinkSigner` Signer State Mutation — (File: `core/contracts/EndpointTx.sol`)

---

### Summary
Both the fast-mode and slow-mode execution paths for the `LinkSigner` transaction type silently update the `linkedSigners` mapping without emitting any on-chain event. Because a linked signer is granted authority to sign transactions on behalf of a subaccount, this is a critical access-control state change with no observable on-chain record.

---

### Finding Description
In `EndpointTx.processTransactionImpl()`, the `LinkSigner` branch decodes the signed transaction, validates the nonce and signature, and then directly writes to `linkedSigners`:

```solidity
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
```

No `emit` statement follows. [1](#0-0) 

The slow-mode path in `processSlowModeTransactionImpl()` has the identical omission:

```solidity
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [2](#0-1) 

Scanning the entire event surface of the protocol confirms no `LinkedSignerSet` or equivalent event is defined anywhere — not in `IEndpoint`, `IClearinghouseEventEmitter`, `IOffchainExchange`, or any other interface. [3](#0-2) [4](#0-3) [5](#0-4) 

By contrast, every other sensitive state mutation in the protocol emits an event: collateral deposits/withdrawals emit `ModifyCollateral`, liquidations emit `Liquidation`, fee-tier changes emit `FeeTierUpdate`, order fills emit `FillOrder`, and public-key assignments in the verifier emit `AssignPubKey`/`DeletePubkey`. The `LinkSigner` path is the sole critical mutation with no event coverage. [6](#0-5) 

---

### Impact Explanation
A linked signer is granted the ability to sign any user-facing transaction on behalf of the subaccount owner (withdrawals, liquidations, NLP mint/burn, transfers). Silently mutating this mapping means:

- Off-chain monitoring infrastructure (indexers, alert bots, user dashboards) has no reliable way to detect when a linked signer is set, changed, or cleared.
- If an attacker obtains a user's key and sets a malicious linked signer, the victim has no on-chain signal to detect the change before the attacker acts.
- Protocol-level audit trails and compliance tooling cannot reconstruct the history of signer delegations.

The corrupted state is the `linkedSigners[subaccount]` entry — a concrete signer-state delta with direct authority over all subsequent signed operations for that subaccount.

---

### Likelihood Explanation
The `LinkSigner` transaction type is reachable by any unprivileged user. A user signs the `LinkSigner` payload off-chain and submits it either through the sequencer (fast mode) or directly via `submitSlowModeTransaction` (slow mode). No owner or admin privilege is required. The slow-mode path is callable by any EOA. [7](#0-6) 

---

### Recommendation
Define a `LinkedSignerSet` event in `IEndpoint` (or a dedicated interface):

```solidity
event LinkedSignerSet(bytes32 indexed subaccount, address indexed signer);
```

Emit it in both execution paths immediately after the `linkedSigners` write:

```solidity
// in processTransactionImpl — LinkSigner branch
linkedSigners[signedTx.tx.sender] = address(uint160(bytes20(signedTx.tx.signer)));
emit LinkedSignerSet(signedTx.tx.sender, address(uint160(bytes20(signedTx.tx.signer))));

// in processSlowModeTransactionImpl — LinkSigner branch
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
emit LinkedSignerSet(txn.sender, address(uint160(bytes20(txn.signer))));
```

Also audit `transferQuote`, `mintNlp`, and `burnNlp` in `Clearinghouse.sol` for the same pattern — all three mutate significant financial state without emitting dedicated high-level events. [8](#0-7) [9](#0-8) [10](#0-9) 

---

### Proof of Concept

1. User signs a `LinkSigner` payload designating `attacker_address` as the new signer for their subaccount.
2. User (or attacker with the user's key) submits the transaction via `submitSlowModeTransaction` or the sequencer submits it via `submitTransactionsChecked`.
3. `processTransactionImpl` (or `processSlowModeTransactionImpl`) executes the `LinkSigner` branch.
4. `linkedSigners[subaccount]` is updated to `attacker_address`. [11](#0-10) 
5. No event is emitted. The victim's wallet, any indexer, and any monitoring bot receive zero on-chain signal.
6. The attacker now uses `attacker_address` as a linked signer to sign withdrawal or liquidation transactions on behalf of the victim's subaccount — all validated by `getLinkedSigner` which reads the silently-mutated mapping. [12](#0-11)

### Citations

**File:** core/contracts/EndpointTx.sol (L143-157)
```text
    function getLinkedSigner(bytes32 subaccount)
        public
        view
        virtual
        returns (address)
    {
        return
            RiskHelper.isIsolatedSubaccount(subaccount)
                ? linkedSigners[
                    IOffchainExchange(offchainExchange).getParentSubaccount(
                        subaccount
                    )
                ]
                : linkedSigners[subaccount];
    }
```

**File:** core/contracts/EndpointTx.sol (L232-239)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
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

**File:** core/contracts/EndpointTx.sol (L576-590)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLinkSigner)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            linkedSigners[signedTx.tx.sender] = address(
                uint160(bytes20(signedTx.tx.signer))
            );
```

**File:** core/contracts/interfaces/IEndpoint.sol (L7-9)
```text
    event SubmitTransactions();
    event PriceQuery(uint32 productId);

```

**File:** core/contracts/interfaces/clearinghouse/IClearinghouseEventEmitter.sol (L1-23)
```text
// SPDX-License-Identifier: GPL-2.0-or-later
pragma solidity ^0.8.0;

interface IClearinghouseEventEmitter {
    /// @notice Emitted during initialization
    event ClearinghouseInitialized(address endpoint, address quote);

    /// @notice Emitted when collateral is modified for a subaccount
    event ModifyCollateral(
        int128 amount,
        bytes32 indexed subaccount,
        uint32 productId
    );

    event Liquidation(
        bytes32 indexed liquidatorSubaccount,
        bytes32 indexed liquidateeSubaccount,
        uint32 productId,
        bool isEncodedSpread,
        int128 amount,
        int128 amountQuote
    );
}
```

**File:** core/contracts/interfaces/IOffchainExchange.sol (L7-53)
```text
    event FillOrder(
        uint32 indexed productId,
        // original order information
        bytes32 indexed digest,
        bytes32 indexed subaccount,
        int128 priceX18,
        int128 amount,
        uint64 expiration,
        uint64 nonce,
        uint128 appendix,
        bool isolated,
        // whether this order is taking or making
        bool isTaker,
        // amount paid in fees (in quote)
        int128 feeAmount,
        // change in this subaccount's base balance from this fill
        int128 baseDelta,
        // change in this subaccount's quote balance from this fill
        int128 quoteDelta
    );

    event CloseIsolatedSubaccount(
        bytes32 indexed isolatedSubaccount,
        bytes32 indexed parentSubaccount
    );

    event FeeTierUpdate(address indexed user, uint32 feeTier);

    event BuilderFeePayment(
        bytes32 indexed subaccount,
        uint32 indexed builder,
        uint32 indexed productId,
        bytes32 digest,
        int128 builderFee,
        int128 fee,
        int128 quoteDelta
    );

    event BuilderUpdate(uint32 indexed builder, address indexed owner);

    event ClaimBuilderFee(
        uint32 indexed builder,
        uint32 indexed productId,
        bytes32 subaccount,
        int128 amount
    );

```

**File:** core/contracts/Clearinghouse.sol (L211-250)
```text
    function transferQuote(IEndpoint.TransferQuote calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 toTransfer = int128(txn.amount);
        ISpotEngine spotEngine = _spotEngine();

        // require the sender address to be the same as the recipient address
        // otherwise linked signers can transfer out
        require(
            bytes20(txn.sender) == bytes20(txn.recipient),
            ERR_UNAUTHORIZED
        );
        address offchainExchange = IEndpoint(getEndpoint())
            .getOffchainExchange();
        if (RiskHelper.isIsolatedSubaccount(txn.sender)) {
            // isolated subaccounts can only transfer quote back to parent
            require(
                IOffchainExchange(offchainExchange).getParentSubaccount(
                    txn.sender
                ) == txn.recipient,
                ERR_UNAUTHORIZED
            );
        } else if (RiskHelper.isIsolatedSubaccount(txn.recipient)) {
            // regular subaccounts can transfer quote to active isolated subaccounts
            require(
                IOffchainExchange(offchainExchange).isIsolatedSubaccountActive(
                    txn.sender,
                    txn.recipient
                ),
                ERR_UNAUTHORIZED
            );
        }

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -toTransfer);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient, toTransfer);
        require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
    }
```

**File:** core/contracts/Clearinghouse.sol (L453-483)
```text
    function mintNlp(
        IEndpoint.MintNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] >= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);

        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```

**File:** core/contracts/Clearinghouse.sol (L485-530)
```text
    function burnNlp(
        IEndpoint.BurnNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.nlpAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 nlpAmount = int128(txn.nlpAmount);
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, -quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] <= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);

        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
        }

        require(
            spotEngine.getBalance(NLP_PRODUCT_ID, txn.sender).amount >= 0,
            ERR_SUBACCT_HEALTH
        );
        // Burning NLP can decrease health if the burn fee exceeds the health improvement
        // from the withdrawal. This check prevents malicious actors from deliberately
        // creating unhealthy subaccounts through NLP burns.
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```
