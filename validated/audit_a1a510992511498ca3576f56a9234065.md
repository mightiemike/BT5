### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables usdcE Theft Without Providing USDC — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` without checking the return value. If the USDC token returns `false` on failure (rather than reverting), the function continues to withdraw usdcE from the `DirectDepositV1` contract and transfer it to `msg.sender` — without `msg.sender` ever having provided USDC. Because the function has no access control, any unprivileged caller can exploit this to drain usdcE from any `DirectDepositV1` contract.

---

### Finding Description

`replaceUsdcEWithUsdc` is an externally callable function with no `onlyOwner` or similar guard: [1](#0-0) 

The intended flow is:
1. Read the usdcE balance held by a user's `DirectDepositV1` (DDA) contract.
2. Pull that same amount of USDC from `msg.sender` into the DDA.
3. Withdraw usdcE from the DDA to `ContractOwner`.
4. Forward the usdcE to `msg.sender`.

Step 2 is the critical step. It is implemented as a bare `transferFrom` call whose boolean return value is silently discarded: [2](#0-1) 

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

Contrast this with every other token transfer in the codebase, which uses the `safeTransferFrom` wrapper from `ERC20Helper` that asserts the return value: [3](#0-2) 

If the USDC token on chain 57073 (Ink) returns `false` on a failed transfer instead of reverting — a behaviour permitted by the ERC-20 specification and exhibited by several deployed tokens — execution continues unconditionally into steps 3 and 4:

```solidity
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));  // pulls usdcE to ContractOwner
IERC20Base(usdcE).safeTransfer(msg.sender, balance);             // sends usdcE to attacker
``` [4](#0-3) 

The `DirectDepositV1.withdraw` function transfers the entire usdcE balance of the DDA to its owner (`ContractOwner`), which then forwards it to `msg.sender`: [5](#0-4) 

---

### Impact Explanation

An attacker with zero USDC and zero allowance calls `replaceUsdcEWithUsdc(victimSubaccount)`. If `transferFrom` returns `false` silently, the attacker receives the full usdcE balance of the victim's DDA contract for free. The victim's DDA loses its usdcE collateral with no USDC deposited in return. This is a direct, unrecoverable theft of user collateral tokens.

---

### Likelihood Explanation

- The function is `external` with no access control — any EOA or contract can call it.
- The only precondition is that a DDA exists with a non-zero usdcE balance.
- The exploitability depends on whether the USDC contract at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink (chain 57073) returns `false` or reverts on failure. ERC-20 permits both behaviours; the contract incorrectly assumes the revert path.
- Even if the current USDC deployment reverts, the missing return-value check is a latent integration bug that would become exploitable if the token contract is ever upgraded or replaced with one that returns `false`.

---

### Recommendation

Replace the bare `transferFrom` call with the project's own `safeTransferFrom` helper (or OpenZeppelin's `SafeERC20.safeTransferFrom`) so that a failed transfer always reverts:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

Additionally, consider adding an `onlyOwner` modifier to `replaceUsdcEWithUsdc`, since it is a privileged migration operation that should not be callable by arbitrary users.

---

### Proof of Concept

1. A victim's DDA (`directDepositV1Address[victimSubaccount]`) holds 1000 usdcE.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(victimSubaccount)` with zero USDC balance and zero allowance.
3. `IERC20Base(usdc).transferFrom(attacker, dda, 1000)` returns `false` (no revert).
4. `DirectDepositV1(dda).withdraw(usdcE)` transfers 1000 usdcE from DDA → ContractOwner.
5. `IERC20Base(usdcE).safeTransfer(attacker, 1000)` transfers 1000 usdcE to the attacker.
6. Attacker receives 1000 usdcE; victim's DDA is drained; no USDC was ever deposited.

### Citations

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
