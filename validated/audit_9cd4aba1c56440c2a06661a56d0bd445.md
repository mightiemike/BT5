### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables USDC.e Drain from User DDAs — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc()` is a publicly callable function (no `onlyOwner` guard) that is intended to atomically swap USDC.e held in a user's Direct Deposit Address (DDA) for native USDC provided by the caller. The inbound USDC leg at line 616 uses a raw `transferFrom` call whose boolean return value is never checked. If the USDC token returns `false` instead of reverting, the function silently skips the inbound transfer and still executes the outbound USDC.e withdrawal, allowing any caller to drain USDC.e from any DDA without providing USDC.

---

### Finding Description

`replaceUsdcEWithUsdc` is the only transfer call in the entire contract that does **not** use the `ERC20Helper.safeTransfer` / `safeTransferFrom` wrappers that the rest of the codebase relies on. Every other transfer in `ContractOwner`, `BaseWithdrawPool`, `EndpointStorage`, and `Clearinghouse` goes through `ERC20Helper`, which low-level-calls the token and `require`s `success && (data.length == 0 || abi.decode(data, (bool)))`. [1](#0-0) 

At line 616 the raw interface method is called instead:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance); // return value discarded
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));       // USDC.e leaves DDA → ContractOwner
IERC20Base(usdcE).safeTransfer(msg.sender, balance);                 // USDC.e sent to caller
``` [2](#0-1) 

The `IERC20Base` interface declares `transferFrom` as returning `bool`. [3](#0-2) 

If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` (Ink chain 57073) returns `false` on a failed transfer rather than reverting, the Solidity compiler silently discards the value and execution falls through to the two subsequent lines that move USDC.e out of the DDA and into the caller's wallet.

For contrast, `ERC20Helper.safeTransferFrom` enforces the check: [4](#0-3) 

---

### Impact Explanation

**Impact: High.**

The function is supposed to be an atomic 1-for-1 swap: caller provides USDC, DDA's USDC.e is returned to the caller. If the inbound USDC leg fails silently, the outbound USDC.e leg still executes. A caller can drain the full USDC.e balance of any DDA on chain 57073 without providing any USDC. Because `directDepositV1Address` is a public mapping, every DDA address is enumerable on-chain. All DDAs holding USDC.e are at risk simultaneously.

---

### Likelihood Explanation

**Likelihood: Low.**

Exploitability depends on whether the specific USDC deployment at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink (chain 57073) can return `false` rather than revert on a failed transfer. Circle's canonical USDC reverts on failure; however, this is a chain-specific bridged/wrapped deployment whose exact behavior is not guaranteed to match mainnet USDC. The function carries no `onlyOwner` guard — only a chain-ID check — so the attacker-controlled entry path requires no privilege escalation. [5](#0-4) 

---

### Recommendation

Replace the raw `transferFrom` call with the `safeTransferFrom` wrapper already used everywhere else in the codebase:

```solidity
// Before (line 616)
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ERC20Helper.safeTransferFrom` low-level-calls the token and reverts if `success` is false or the decoded return value is false, matching the pattern used in `EndpointStorage.safeTransferFrom` and `BaseWithdrawPool.safeTransferFrom`. [4](#0-3) 

---

### Proof of Concept

1. Identify a DDA on chain 57073 with a non-zero USDC.e balance: `balance = IERC20Base(usdcE).balanceOf(directDepositV1) > 0`.
2. Do **not** approve `ContractOwner` to spend any USDC (or approve 0).
3. Call `ContractOwner.replaceUsdcEWithUsdc(subaccount)`.
4. If the USDC token returns `false` on the `transferFrom` (insufficient allowance / balance) rather than reverting, execution continues past line 616.
5. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` transfers the DDA's full USDC.e balance to `ContractOwner`.
6. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` transfers that USDC.e to the attacker.
7. Attacker receives `balance` USDC.e; the DDA owner loses their USDC.e; no USDC was ever deposited. [6](#0-5)

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

**File:** core/contracts/interfaces/IERC20Base.sol (L25-29)
```text
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);
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
