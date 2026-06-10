#!/usr/bin/env python3
"""
Stock Analysis and Telegram Transmission Orchestrator

Overall Process:
1. Execute time-based (morning/afternoon) trigger batch jobs
2. Generate detailed analysis reports for selected stocks
3. Convert reports to PDF
4. Generate and send telegram channel summary messages
5. Send generated PDF attachments
"""
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Logger configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"orchestrator_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Environment configuration
REPORTS_DIR = Path("reports")
TELEGRAM_MSGS_DIR = Path("telegram_messages")
PDF_REPORTS_DIR = Path("pdf_reports")

# Create directories
REPORTS_DIR.mkdir(exist_ok=True)
TELEGRAM_MSGS_DIR.mkdir(exist_ok=True)
PDF_REPORTS_DIR.mkdir(exist_ok=True)
(TELEGRAM_MSGS_DIR / "sent").mkdir(exist_ok=True)


class StockAnalysisOrchestrator:
    """Stock Analysis and Telegram Transmission Orchestrator"""

    def __init__(self, telegram_config=None):
        """
        Initialize orchestrator

        Args:
            telegram_config: TelegramConfig object (uses default config if None)
        """
        from telegram_config import TelegramConfig

        self.selected_tickers = {}  # Store selected stock information
        self.telegram_config = telegram_config or TelegramConfig(use_telegram=True)
        self._broadcast_tasks = []  # Collect fire-and-forget broadcast tasks

    @staticmethod
    def _parse_report_filename(filename_stem: str) -> dict:
        """
        Parse report filename to extract components.

        Expected format: {ticker}_{company_name}_{date}_{mode}_gpt5.2
        Example: 005930_삼성전자_20250127_morning_gpt5.2

        Args:
            filename_stem: Filename without extension

        Returns:
            dict with keys: ticker, company_name, date, mode, suffix, valid
        """
        result = {
            'ticker': '',
            'company_name': '',
            'date': '',
            'mode': '',
            'suffix': '',
            'valid': False
        }

        try:
            parts = filename_stem.split('_')
            if len(parts) < 4:
                return result

            # Find date position (8-digit number)
            date_idx = -1
            for i, part in enumerate(parts):
                if len(part) == 8 and part.isdigit():
                    date_idx = i
                    break

            if date_idx < 2:
                return result

            # Extract components
            result['ticker'] = parts[0]
            result['company_name'] = '_'.join(parts[1:date_idx])  # Handle company names with underscores
            result['date'] = parts[date_idx]
            result['mode'] = parts[date_idx + 1] if date_idx + 1 < len(parts) else ''
            result['suffix'] = '_'.join(parts[date_idx + 2:]) if date_idx + 2 < len(parts) else ''
            result['valid'] = True

        except Exception as e:
            logger.warning(f"Failed to parse filename '{filename_stem}': {str(e)}")

        return result

    async def _create_translated_filename(self, original_path: Path, target_lang: str) -> Path:
        """
        Create translated filename with English company name.

        Args:
            original_path: Original file path
            target_lang: Target language code (e.g., "en")

        Returns:
            Path with translated filename
        """
        from cores.company_name_translator import translate_company_name

        # Parse original filename
        parsed = self._parse_report_filename(original_path.stem)

        if not parsed['valid']:
            # Fallback: just append language code
            logger.warning(f"Could not parse filename, using fallback: {original_path.stem}")
            return original_path.parent / f"{original_path.stem}_{target_lang}.md"

        # Translate company name (only for English)
        if not parsed['company_name']:
            logger.warning(f"Empty company name in filename: {original_path.stem}")
            # Try to get company name from pykrx
            try:
                from pykrx import stock as stock_api
                parsed['company_name'] = stock_api.get_market_ticker_name(parsed['ticker']) or ""
                logger.info(f"Retrieved company name from pykrx: {parsed['company_name']}")
            except Exception:
                pass

        if target_lang == "en":
            # Translate Korean company name to English for English channel
            translated_name = await translate_company_name(parsed['company_name']) if parsed['company_name'] else ""
        else:
            # For other languages (ja, zh, etc.), also translate to English for filename compatibility
            # This ensures PDF filenames don't contain Korean characters in any broadcast channel
            translated_name = await translate_company_name(parsed['company_name']) if parsed['company_name'] else ""

        # Reconstruct filename
        # Format: {ticker}_{translated_company}_{date}_{mode}_{suffix}_{lang}.md
        new_stem_parts = [parsed['ticker'], translated_name, parsed['date'], parsed['mode']]
        if parsed['suffix']:
            new_stem_parts.append(parsed['suffix'])
        new_stem_parts.append(target_lang)

        new_stem = '_'.join(filter(None, new_stem_parts))
        new_path = original_path.parent / f"{new_stem}.md"

        logger.info(f"Translated filename: {original_path.name} → {new_path.name}")
        return new_path

    @staticmethod
    def _extract_base64_images(markdown_text: str) -> tuple[str, dict]:
        """
        Extract base64 images from markdown and replace with placeholders

        Args:
            markdown_text: Original markdown text with base64 images

        Returns:
            Tuple of (text_without_images, images_dict)
        """
        images = {}
        counter = 0

        def replace_image(match):
            nonlocal counter
            # Use XML-style placeholder that won't be translated
            placeholder = f"<<<__BASE64_IMAGE_{counter}__>>>"
            images[placeholder] = match.group(0)  # Store entire image markdown
            logger.info(f"Extracted image {counter}, size: {len(match.group(0))} chars")
            counter += 1
            return placeholder

        # Pattern to match base64 images in HTML img tags: <img src="data:image/...;base64,..." ... />
        # Also supports markdown format: ![alt](data:image/...;base64,...)
        patterns = [
            r'<img\s+src="data:image/[^;]+;base64,[A-Za-z0-9+/=]+"\s+[^>]*>',  # HTML img tag
            r'!\[([^\]]*)\]\(data:image/[^;]+;base64,[A-Za-z0-9+/=]+\)',  # Markdown format
        ]

        text_without_images = markdown_text
        for pattern in patterns:
            text_without_images = re.sub(pattern, replace_image, text_without_images)

        logger.info(f"Extracted {len(images)} base64 images from markdown")
        return text_without_images, images

    @staticmethod
    def _restore_base64_images(translated_text: str, images: dict) -> str:
        """
        Restore base64 images to translated text

        Args:
            translated_text: Translated text with placeholders
            images: Dictionary of placeholder -> original image markdown

        Returns:
            Text with restored images
        """
        restored_text = translated_text

        # First try exact match
        for placeholder, original_image in images.items():
            if placeholder in restored_text:
                restored_text = restored_text.replace(placeholder, original_image)
                logger.debug(f"Restored image (exact match): {placeholder}")
            else:
                # Fallback: look for translated variations like [Image: ...] or ![...]
                # Extract the image number from placeholder
                import re
                match = re.search(r'<<<__BASE64_IMAGE_(\d+)__>>>', placeholder)
                if match:
                    img_num = match.group(1)
                    # Look for common translation patterns (both HTML and markdown)
                    patterns = [
                        rf'<img\s+[^>]*>',  # HTML img tag (translated or not)
                        rf'\[Image[^\]]*\]',  # [Image: ...]
                        rf'!\[[^\]]*\]\([^\)]*\)',  # ![alt](url) that's not base64
                        rf'\[图片[^\]]*\]',  # Chinese: [图片...]
                        rf'\[画像[^\]]*\]',  # Japanese: [画像...]
                    ]

                    replaced = False
                    for pattern in patterns:
                        # Find the Nth occurrence based on img_num
                        matches = list(re.finditer(pattern, restored_text))
                        if int(img_num) < len(matches):
                            match_obj = matches[int(img_num)]
                            # Replace this specific match
                            before = restored_text[:match_obj.start()]
                            after = restored_text[match_obj.end():]
                            restored_text = before + original_image + after
                            logger.info(f"Restored image {img_num} using fallback pattern: {pattern}")
                            replaced = True
                            break

                    if not replaced:
                        logger.warning(f"Could not restore image {img_num}, placeholder not found: {placeholder}")

        logger.info(f"Restored {len(images)} base64 images to translated text")
        return restored_text

    async def run_trigger_batch(self, mode, exclude_codes: list = None):
        """
        Execute trigger batch and save results (direct import version)

        Uses direct import instead of subprocess to share KRX session,
        reducing login attempts.

        Args:
            mode (str): 'morning' or 'afternoon'
            exclude_codes (list): List of stock codes to exclude (already in portfolio)

        Returns:
            list: List of selected stock codes
        """
        logger.info(f"Starting trigger batch execution: {mode}")
        try:
            # Direct import instead of subprocess to share KRX session
            from trigger_batch import run_batch

            # Results file path
            results_file = f"trigger_results_{mode}_{datetime.now().strftime('%Y%m%d')}.json"

            # Run batch directly (synchronous call in async context)
            # run_batch is CPU-bound, so running it directly is acceptable
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: run_batch(mode, "INFO", results_file, exclude_codes)
            )

            if not results:
                logger.warning("Batch returned empty results")
                return []

            # Read results file for full data with metadata
            if os.path.exists(results_file):
                with open(results_file, 'r', encoding='utf-8') as f:
                    full_results = json.load(f)
                # Save results
                self.selected_tickers[mode] = full_results

            # Extract stock codes from results
            tickers = []
            ticker_codes = set()  # For duplicate checking

            # results is dict like {"Volume Surge Top Stocks": DataFrame, ...}
            for trigger_type, stocks_df in results.items():
                if hasattr(stocks_df, 'index'):  # It's a DataFrame
                    for ticker in stocks_df.index:
                        if ticker not in ticker_codes:
                            ticker_codes.add(ticker)
                            # Get stock name (with fallback to pykrx API)
                            name = ""
                            # Support both Korean and English column names
                            name_col = None
                            if "Company Name" in stocks_df.columns:
                                name_col = "Company Name"
                            elif "종목명" in stocks_df.columns:
                                name_col = "종목명"

                            if name_col:
                                name = stocks_df.loc[ticker, name_col]
                            # Fallback: use pykrx API if name is empty
                            if not name:
                                try:
                                    from pykrx import stock as stock_api
                                    name = stock_api.get_market_ticker_name(ticker) or ""
                                except Exception:
                                    pass

                            # Get risk_reward_ratio if available
                            rr_ratio = 0
                            if "Risk/Reward Ratio" in stocks_df.columns or "손익비" in stocks_df.columns:
                                col_name = "Risk/Reward Ratio" if "Risk/Reward Ratio" in stocks_df.columns else "손익비"
                                try:
                                    val = stocks_df.loc[ticker, col_name]
                                    if isinstance(val, str):
                                        val = val.replace(',', '').replace(' ', '')
                                    # NaN check and float conversion
                                    rr_ratio = float(val) if not (isinstance(val, float) and val != val) else 0.0
                                except Exception:
                                    rr_ratio = 0.0

                            # Get volume_profile_info if available
                            volume_profile_info = ""
                            if "volume_profile_info" in stocks_df.columns:
                                volume_profile_info = str(stocks_df.loc[ticker, "volume_profile_info"])

                            is_fallback = False
                            if "is_fallback" in stocks_df.columns:
                                is_fallback = bool(stocks_df.loc[ticker, "is_fallback"])

                            tickers.append({
                                'code': ticker,
                                'name': name,
                                'trigger_type': trigger_type,
                                'trigger_mode': mode,
                                'risk_reward_ratio': rr_ratio,
                                'volume_profile_info': volume_profile_info,
                                'is_fallback': is_fallback
                            })

            logger.info(f"Number of selected stocks: {len(tickers)}")
            return tickers

        except Exception as e:
            logger.error(f"Error during trigger batch execution: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    async def convert_to_pdf(self, report_paths):
        """
        Convert markdown reports to PDF

        Args:
            report_paths (list): List of markdown report file paths

        Returns:
            list: List of generated PDF file paths
        """
        logger.info(f"Starting PDF conversion for {len(report_paths)} reports")
        pdf_paths = []

        # Import PDF converter module
        from pdf_converter import markdown_to_pdf

        for report_path in report_paths:
            try:
                report_file = Path(report_path)
                pdf_file = PDF_REPORTS_DIR / f"{report_file.stem}.pdf"

                # Convert markdown to PDF
                markdown_to_pdf(report_path, pdf_file, 'playwright', add_theme=True, enable_watermark=False)

                logger.info(f"PDF conversion complete: {pdf_file}")
                pdf_paths.append(pdf_file)

            except Exception as e:
                logger.error(f"Error during PDF conversion of {report_path}: {str(e)}")

        return pdf_paths

    async def generate_telegram_messages(self, report_pdf_paths, language: str = "ko"):
        """
        Generate telegram messages

        Args:
            report_pdf_paths (list): List of report file (pdf) paths
            language (str): Message language ("ko" or "en")

        Returns:
            list: List of generated telegram message file paths
        """
        logger.info(f"Starting telegram message generation for {len(report_pdf_paths)} reports (language: {language})")

        # Import telegram summary generator module
        from telegram_summary_agent import TelegramSummaryGenerator

        # Initialize summary generator
        generator = TelegramSummaryGenerator()

        message_paths = []
        for report_pdf_path in report_pdf_paths:
            try:
                # Generate telegram message
                await generator.process_report(str(report_pdf_path), str(TELEGRAM_MSGS_DIR), to_lang=language)

                # Estimate generated message file path
                report_file = Path(report_pdf_path)
                ticker = report_file.stem.split('_')[0]
                company_name = report_file.stem.split('_')[1]

                message_path = TELEGRAM_MSGS_DIR / f"{ticker}_{company_name}_telegram.txt"

                if message_path.exists():
                    logger.info(f"Telegram message generation complete: {message_path}")
                    message_paths.append(message_path)
                else:
                    logger.warning(f"Telegram message file not found at expected path: {message_path}")

            except Exception as e:
                logger.error(f"Error during telegram message generation for {report_pdf_path}: {str(e)}")

        return message_paths
    async def send_telegram_message_only(message: str):
        from telegram_bot_agent import TelegramBotAgent
        import os
        from dotenv import load_dotenv

        # env파일 로드
        load_dotenv(dotenv_path=str('./.env'))


        # 텔레그램 설정
        telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHANNEL_ID")

        if not telegram_token:
            raise ValueError("텔레그램 봇 토큰이 필요합니다. 환경 변수 TELEGRAM_BOT_TOKEN 또는 파라미터로 제공해주세요.")

        if not chat_id:
            raise ValueError("텔레그램 채널 ID가 필요합니다. 환경 변수 TELEGRAM_CHANNEL_ID 또는 파라미터로 제공해주세요.")

        telegram_bot = TelegramBotAgent(token=telegram_token)
        # 텔레그램 전송
        success = await telegram_bot.send_message(chat_id, message)

        if success:
            print("텔레그램 전송 성공!")
            return True
        else:
            print("텔레그램 전송 실패!")
            return False


    async def send_telegram_messages(self, message_paths, pdf_paths, report_paths=None):
        """
        Send telegram messages and PDF files

        Args:
            message_paths (list): List of telegram message file paths
            pdf_paths (list): List of PDF file paths
            report_paths (list): List of markdown report file paths (for translation)
        """
        # Skip if telegram is disabled
        if not self.telegram_config.use_telegram:
            logger.info(f"Telegram disabled - skipping message and PDF transmission")
            return

        logger.info(f"Starting telegram message transmission for {len(message_paths)} messages")

        # Use telegram configuration
        chat_id = self.telegram_config.channel_id
        if not chat_id:
            logger.error("Telegram channel ID is not configured.")
            return

        # Initialize telegram bot agent
        from telegram_bot_agent import TelegramBotAgent

        try:
            bot_agent = TelegramBotAgent()

            # Pre-read message contents into memory for non-blocking broadcast translation
            if self.telegram_config.broadcast_languages:
                message_contents = []
                for mp in message_paths:
                    try:
                        with open(mp, 'r', encoding='utf-8') as f:
                            message_contents.append(f.read())
                    except Exception as e:
                        logger.error(f"Error reading message file {mp}: {str(e)}")
                if message_contents:
                    self._broadcast_tasks.append(
                        asyncio.create_task(self._send_translated_messages(bot_agent, message_contents))
                    )

            # Send messages to main channel (this moves files to sent folder)
            await bot_agent.process_messages_directory(
                str(TELEGRAM_MSGS_DIR),
                chat_id,
                str(TELEGRAM_MSGS_DIR / "sent"),
                msg_type="analysis"
            )

            # Send PDF files to main channel
            for pdf_path in pdf_paths:
                logger.info(f"Sending PDF file: {pdf_path}")
                success = await bot_agent.send_document(chat_id, str(pdf_path), msg_type="pdf")
                if success:
                    logger.info(f"PDF file transmission successful: {pdf_path}")
                else:
                    logger.error(f"PDF file transmission failed: {pdf_path}")

                # Transmission interval
                await asyncio.sleep(1)

            # Send translated PDFs to broadcast channels asynchronously (non-blocking)
            if self.telegram_config.broadcast_languages and report_paths:
                self._broadcast_tasks.append(
                        asyncio.create_task(self._send_translated_pdfs(bot_agent, report_paths))
                    )

        except Exception as e:
            logger.error(f"Error during telegram message transmission: {str(e)}")

    async def _send_translated_messages(self, bot_agent, message_contents):
        """
        Send translated telegram messages to broadcast channels (non-blocking, fire-and-forget)
        Languages are processed in parallel for faster delivery.

        Args:
            bot_agent: TelegramBotAgent instance
            message_contents: List of original message content strings (pre-read from files)
        """
        try:
            from cores.agents.telegram_translator_agent import translate_telegram_message

            async def _translate_and_send_lang(lang, channel_id):
                for original_message in message_contents:
                    try:
                        logger.info(f"Translating telegram message to {lang}")
                        translated_message = await translate_telegram_message(
                            original_message,
                            model="gpt-5-nano",
                            from_lang="ko",
                            to_lang=lang
                        )
                        success = await bot_agent.send_message(channel_id, translated_message, msg_type="analysis")
                        if success:
                            logger.info(f"Telegram message sent successfully to {lang} channel")
                        else:
                            logger.error(f"Failed to send telegram message to {lang} channel")
                        await asyncio.sleep(1)
                    except Exception as e:
                        logger.error(f"Error translating/sending message to {lang}: {str(e)}")

            lang_tasks = []
            for lang in self.telegram_config.broadcast_languages:
                channel_id = self.telegram_config.get_broadcast_channel_id(lang)
                if not channel_id:
                    logger.warning(f"No channel ID configured for language: {lang}")
                    continue
                logger.info(f"Dispatching parallel translation for {lang} channel")
                lang_tasks.append(_translate_and_send_lang(lang, channel_id))

            if lang_tasks:
                await asyncio.gather(*lang_tasks, return_exceptions=True)

        except Exception as e:
            logger.error(f"Error in _send_translated_messages: {str(e)}")

    async def _send_translated_pdfs(self, bot_agent, report_paths):
        """
        Send translated PDF reports to broadcast channels (asynchronous, runs in background)
        Languages are processed in parallel for faster delivery.

        Args:
            bot_agent: TelegramBotAgent instance
            report_paths: List of original markdown report file paths
        """
        try:
            from cores.agents.telegram_translator_agent import translate_telegram_message

            async def _translate_pdfs_for_lang(lang, channel_id):
                for report_path in report_paths:
                    try:
                        logger.info(f"Translating markdown report {report_path} to {lang}")

                        with open(report_path, 'r', encoding='utf-8') as f:
                            original_report = f.read()

                        text_for_translation, images = self._extract_base64_images(original_report)
                        logger.info(f"Prepared report for translation: {len(text_for_translation)} chars (extracted {len(images)} images)")

                        translated_report = await translate_telegram_message(
                            text_for_translation,
                            model="gpt-5-nano",
                            from_lang="ko",
                            to_lang=lang
                        )

                        translated_report = self._restore_base64_images(translated_report, images)
                        logger.info(f"Restored images to translated report: {len(translated_report)} chars")

                        report_file = Path(report_path)
                        translated_report_path = await self._create_translated_filename(report_file, lang)

                        with open(translated_report_path, 'w', encoding='utf-8') as f:
                            f.write(translated_report)

                        logger.info(f"Translated report saved: {translated_report_path}")

                        translated_pdf_paths = await self.convert_to_pdf([str(translated_report_path)])

                        if translated_pdf_paths and len(translated_pdf_paths) > 0:
                            translated_pdf_path = translated_pdf_paths[0]
                            logger.info(f"Sending translated PDF {translated_pdf_path} to {lang} channel")
                            success = await bot_agent.send_document(channel_id, str(translated_pdf_path), msg_type="pdf")

                            if success:
                                logger.info(f"Translated PDF sent successfully to {lang} channel")
                            else:
                                logger.error(f"Failed to send translated PDF to {lang} channel")

                            await asyncio.sleep(1)
                        else:
                            logger.error(f"Failed to convert translated report to PDF: {translated_report_path}")

                    except Exception as e:
                        logger.error(f"Error processing report {report_path} for {lang}: {str(e)}")

            # Process languages sequentially to limit memory usage
            # (each PDF generation spawns a Playwright/Chromium instance)
            for lang in self.telegram_config.broadcast_languages:
                channel_id = self.telegram_config.get_broadcast_channel_id(lang)
                if not channel_id:
                    logger.warning(f"No channel ID configured for language: {lang}")
                    continue
                logger.info(f"Processing PDF translation for {lang} channel (sequential)")
                try:
                    await _translate_pdfs_for_lang(lang, channel_id)
                except Exception as lang_err:
                    logger.error(f"PDF translation failed for {lang}: {lang_err}")

        except Exception as e:
            logger.error(f"Error in _send_translated_pdfs: {str(e)}")

    async def send_trigger_alert(self, mode, trigger_results_file, language: str = "ko"):
        """
        Send trigger execution result information to telegram channel immediately

        Args:
            mode: 'morning' or 'afternoon'
            trigger_results_file: Path to trigger results JSON file
            language: Message language ("ko" or "en")
        """
        # Log and return if telegram is disabled
        if not self.telegram_config.use_telegram:
            logger.info(f"Telegram disabled - skipping Prism Signal alert transmission (mode: {mode})")
            return False

        logger.info(f"Starting Prism Signal alert transmission - mode: {mode}, language: {language}")

        try:
            # Read JSON file
            with open(trigger_results_file, 'r', encoding='utf-8') as f:
                results = json.load(f)

            # Extract metadata
            metadata = results.get("metadata", {})
            trade_date = metadata.get("trade_date", datetime.now().strftime("%Y%m%d"))

            # Extract trigger stock information - handle direct list case
            all_results = {}
            for key, value in results.items():
                if key != "metadata" and isinstance(value, list):
                    # When value is directly a stock list
                    all_results[key] = value

            if not all_results:
                logger.warning(f"No trigger results found.")
                return False

            # Generate telegram message
            message = self._create_trigger_alert_message(mode, all_results, trade_date)

            # Translate message if English is requested
            if language == "en":
                try:
                    logger.info("Translating trigger alert message to English")
                    from cores.agents.telegram_translator_agent import translate_telegram_message
                    message = await translate_telegram_message(message, model="gpt-5-nano")
                    logger.info("Translation complete")
                except Exception as e:
                    logger.error(f"Translation failed: {str(e)}. Using original Korean message.")

            # Use telegram configuration
            chat_id = self.telegram_config.channel_id
            if not chat_id:
                logger.error("Telegram channel ID is not configured.")
                return False

            # Initialize telegram bot agent
            from telegram_bot_agent import TelegramBotAgent

            try:
                bot_agent = TelegramBotAgent()

                # Send message to main channel
                success = await bot_agent.send_message(chat_id, message, msg_type="trigger")

                if success:
                    logger.info("Prism Signal alert transmission successful")
                else:
                    logger.error("Prism Signal alert transmission failed")

                # Send to broadcast channels asynchronously (non-blocking)
                if self.telegram_config.broadcast_languages:
                    self._broadcast_tasks.append(
                        asyncio.create_task(self._send_translated_trigger_alert(bot_agent, message, mode))
                    )

                return success

            except Exception as e:
                logger.error(f"Error during telegram bot initialization or message transmission: {str(e)}")
                return False

        except Exception as e:
            logger.error(f"Error during Prism Signal alert generation: {str(e)}")
            return False

    async def _send_translated_trigger_alert(self, bot_agent, original_message: str, mode: str):
        """
        Send translated trigger alerts to additional language channels.
        Languages are processed in parallel for faster delivery.

        Args:
            bot_agent: TelegramBotAgent instance
            original_message: Original Korean message
            mode: 'morning' or 'afternoon'
        """
        try:
            from cores.agents.telegram_translator_agent import translate_telegram_message

            async def _translate_and_send_lang(lang, channel_id):
                try:
                    logger.info(f"Translating trigger alert to {lang}")
                    translated_message = await translate_telegram_message(
                        original_message,
                        model="gpt-5-nano",
                        from_lang="ko",
                        to_lang=lang
                    )
                    success = await bot_agent.send_message(channel_id, translated_message, msg_type="trigger")
                    if success:
                        logger.info(f"Trigger alert sent successfully to {lang} channel")
                    else:
                        logger.error(f"Failed to send trigger alert to {lang} channel")
                except Exception as e:
                    logger.error(f"Error sending translated trigger alert to {lang}: {str(e)}")

            lang_tasks = []
            for lang in self.telegram_config.broadcast_languages:
                channel_id = self.telegram_config.get_broadcast_channel_id(lang)
                if not channel_id:
                    logger.warning(f"No channel ID configured for language: {lang}")
                    continue
                lang_tasks.append(_translate_and_send_lang(lang, channel_id))

            if lang_tasks:
                await asyncio.gather(*lang_tasks, return_exceptions=True)

        except Exception as e:
            logger.error(f"Error in _send_translated_trigger_alert: {str(e)}")

    def _create_trigger_alert_message(self, mode, results, trade_date):
        """
        Generate telegram alert message based on trigger results
        """
        # Convert date format
        formatted_date = f"{trade_date[:4]}.{trade_date[4:6]}.{trade_date[6:8]}"

        # Set title based on mode
        if mode == "morning":
            title = "🔔 오전 프리즘 시그널 얼럿"
            time_desc = "장 시작 후 10분 시점"
        else:
            title = "🔔 오후 프리즘 시그널 얼럿"
            time_desc = "장 마감 후"

        # Message header
        message = f"{title}\n"
        message += f"📅 {formatted_date} {time_desc} 포착된 관심종목\n\n"

        # Add stock information by trigger
        for trigger_type, stocks in results.items():
            # Set emoji based on trigger type
            emoji = self._get_trigger_emoji(trigger_type)

            message += f"{emoji} *{trigger_type}*\n"

            # Add each stock information
            for stock in stocks:
                code = stock.get("code", "")
                name = stock.get("name", "")
                current_price = stock.get("current_price", 0)
                change_rate = stock.get("change_rate", 0)

                # Arrow based on change rate
                arrow = "⬆️" if change_rate > 0 else "⬇️" if change_rate < 0 else "➖"

                # Basic information
                message += f"· *{name}* ({code})\n"
                message += f"  {current_price:,.0f}원 {arrow} {abs(change_rate):.2f}%\n"

                # Additional information based on trigger type
                if "volume_increase" in stock and ("Volume" in trigger_type or "거래량" in trigger_type):
                    volume_increase = stock.get("volume_increase", 0)
                    message += f"  거래량 증가율: {volume_increase:.2f}%\n"

                elif "gap_rate" in stock and ("Gap" in trigger_type or "갭 상승" in trigger_type):
                    gap_rate = stock.get("gap_rate", 0)
                    message += f"  갭 상승률: {gap_rate:.2f}%\n"

                elif "trade_value_ratio" in stock and ("Market Cap" in trigger_type or "시총 대비" in trigger_type):
                    trade_value_ratio = stock.get("trade_value_ratio", 0)
                    market_cap = stock.get("market_cap", 0) / 100000000  # Convert to hundred million won units
                    message += f"  거래대금/시총 비율: {trade_value_ratio:.2f}%\n"
                    message += f"  시가총액: {market_cap:.2f}억원\n"

                elif "closing_strength" in stock and ("Closing Strength" in trigger_type or "마감 강도" in trigger_type):
                    closing_strength = stock.get("closing_strength", 0) * 100
                    message += f"  마감 강도: {closing_strength:.2f}%\n"

                message += "\n"

        # Footer message
        message += "💡 상세 분석 보고서는 약 10-30분 내 제공 예정\n"
        message += "⚠️ 본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다."

        return message

    def _get_trigger_emoji(self, trigger_type):
        """
        Return emoji matching trigger type
        """
        if "Volume" in trigger_type or "거래량" in trigger_type:
            return "📊"
        elif "Gap" in trigger_type or "갭 상승" in trigger_type:
            return "📈"
        elif "Market Cap" in trigger_type or "시총 대비" in trigger_type:
            return "💰"
        elif "Gain" in trigger_type or "상승률" in trigger_type:
            return "🚀"
        elif "Closing Strength" in trigger_type or "마감 강도" in trigger_type:
            return "🔨"
        elif "Sideways" in trigger_type or "횡보" in trigger_type:
            return "↔️"
        else:
            return "🔎"

    async def check_api_credits(self) -> dict:
        """
        Check API keys for OpenAI and Perplexity by running minimal test requests.
        Returns a dictionary with status of each API:
        {"openai": {"ok": bool, "error": Optional[str]}, "perplexity": {"ok": bool, "error": Optional[str]}}
        """
        import yaml
        import aiohttp

        # 1. Load OpenAI API key
        openai_key = os.environ.get("OPENAI_API_KEY")
        if not openai_key:
            secrets_path = Path("mcp_agent.secrets.yaml")
            if secrets_path.exists():
                try:
                    with open(secrets_path, "r", encoding="utf-8") as f:
                        secrets = yaml.safe_load(f)
                        openai_key = secrets.get("openai", {}).get("api_key")
                except Exception as e:
                    logger.warning(f"Error loading mcp_agent.secrets.yaml: {e}")

        # 2. Load Perplexity API key
        perplexity_key = os.environ.get("PERPLEXITY_API_KEY")
        if not perplexity_key:
            config_path = Path("mcp_agent.config.yaml")
            if config_path.exists():
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        config = yaml.safe_load(f)
                        perplexity_key = config.get("mcp", {}).get("servers", {}).get("perplexity", {}).get("env", {}).get("PERPLEXITY_API_KEY")
                except Exception as e:
                    logger.warning(f"Error loading mcp_agent.config.yaml: {e}")

        results = {}

        # Test OpenAI Chat Completion
        if openai_key:
            try:
                headers = {
                    "Authorization": f"Bearer {openai_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "gpt-5-nano",
                    "messages": [{"role": "user", "content": "test"}],
                    "max_completion_tokens": 16,
                    "service_tier": "flex"
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers, timeout=15) as resp:
                        if resp.status == 200:
                            results["openai"] = {"ok": True, "error": None}
                        else:
                            try:
                                resp_json = await resp.json()
                                err_msg = resp_json.get("error", {}).get("message", f"HTTP {resp.status}")
                            except Exception:
                                err_msg = f"HTTP {resp.status}"
                            results["openai"] = {"ok": False, "error": err_msg}
            except Exception as e:
                results["openai"] = {"ok": False, "error": str(e)}
        else:
            results["openai"] = {"ok": False, "error": "OpenAI API Key not found"}

        # Test Perplexity Chat Completion
        if perplexity_key:
            if "example" in perplexity_key or "your_" in perplexity_key:
                results["perplexity"] = {"ok": False, "error": "Perplexity API Key is a placeholder"}
            else:
                try:
                    headers = {
                        "Authorization": f"Bearer {perplexity_key}",
                        "Content-Type": "application/json"
                    }
                    payload = {
                        "model": "sonar",
                        "messages": [{"role": "user", "content": "test"}],
                        "max_tokens": 16
                    }
                    async with aiohttp.ClientSession() as session:
                        async with session.post("https://api.perplexity.ai/chat/completions", json=payload, headers=headers, timeout=15) as resp:
                            if resp.status == 200:
                                results["perplexity"] = {"ok": True, "error": None}
                            else:
                                try:
                                    resp_json = await resp.json()
                                    err_msg = resp_json.get("error", {}).get("message", f"HTTP {resp.status}")
                                except Exception:
                                    err_msg = f"HTTP {resp.status}"
                                results["perplexity"] = {"ok": False, "error": err_msg}
                except Exception as e:
                    results["perplexity"] = {"ok": False, "error": str(e)}
        else:
            results["perplexity"] = {"ok": False, "error": "Perplexity API Key not found"}

        return results

    async def run_full_pipeline(self, mode, language: str = "ko"):
        """
        Execute full pipeline

        Args:
            mode (str): 'morning' or 'afternoon'
            language (str): Analysis language ("ko" or "en")
        """
        logger.info(f"Starting full pipeline - mode: {mode}")

        try:
            # 0. API Credit / Key validity check
            logger.info("Performing pre-flight API credit check for OpenAI and Perplexity...")
            credit_check = await self.check_api_credits()
            openai_ok = credit_check.get("openai", {}).get("ok", False)
            perplexity_ok = credit_check.get("perplexity", {}).get("ok", False)
            
            if not openai_ok or not perplexity_ok:
                alert_msg = "⚠️ [API Credit/Validity Alert]\n\n"
                if not openai_ok:
                    alert_msg += f"• **OpenAI API Key Error**: {credit_check['openai']['error']}\n"
                if not perplexity_ok:
                    alert_msg += f"• **Perplexity API Key Error**: {credit_check['perplexity']['error']}\n"
                alert_msg += "\nPlease check your API keys and credit balances immediately."
                
                logger.error(alert_msg)
                
                # Send telegram message if enabled
                if self.telegram_config.use_telegram:
                    try:
                        from telegram_bot_agent import TelegramBotAgent
                        bot_agent = TelegramBotAgent()
                        chat_id = self.telegram_config.channel_id
                        if chat_id:
                            await bot_agent.send_message(chat_id, alert_msg, parse_mode="Markdown")
                            logger.info("Telegram credit warning message sent successfully.")
                    except Exception as te:
                        logger.error(f"Failed to send credit alert to Telegram: {te}")
                
                logger.error("API credit check failed. Aborting full pipeline execution.")
                return

            is_portfolio_full = False
            try:
                # 0. 현재 보유 중인 포트폴리오 종목 갯수 확인
                import my_portfolio
                from stock_tracking_agent import StockTrackingAgent
                from trading.domestic_stock_trading import DEFAULT_BUY_AMOUNT
                portfolio_count = await my_portfolio.get_portfolio_stock_count()
                total_cash_data = await my_portfolio.get_account()
                total_cash = total_cash_data.get('total_cash') if total_cash_data is not None else None
                logger.info(f"현재 보유 중인 포트폴리오 종목 갯수: {portfolio_count}/{StockTrackingAgent.MAX_SLOTS}")

                is_portfolio_full = portfolio_count >= StockTrackingAgent.MAX_SLOTS
                if total_cash is not None:
                    logger.info(f"현재 총 보유 현금: {total_cash:,.0f}원")

                    if total_cash < DEFAULT_BUY_AMOUNT:
                        logger.warning(f"총 보유 현금이 {DEFAULT_BUY_AMOUNT:,.0f}원 미만입니다. 신규 종목 추가가 제한될 수 있습니다.")
                        is_portfolio_full = True
            except Exception as e:
                logger.error(f"포트폴리오 사전 확인 중 오류: {str(e)}")


            pdf_paths = []
            report_paths = []
            if is_portfolio_full:
                logger.warning("포트폴리오 종목 갯수가 최대치에 도달했습니다. 신규 종목 추가가 제한될 수 있습니다.")
            else:
                # 0. Get portfolio stock codes to exclude
                exclude_codes = []
                try:
                    portfolio_tickers = await my_portfolio.get_portfolio_stock()
                    exclude_codes = [p['ticker'] for p in portfolio_tickers]
                    logger.info(f"Portfolio stocks to exclude: {exclude_codes}")
                except Exception as e:
                    logger.error(f"Error fetching portfolio stocks for exclusion: {str(e)}")

                # 1. Execute trigger batch - changed to async method (improved asyncio resource management)
                results_file = f"trigger_results_{mode}_{datetime.now().strftime('%Y%m%d')}.json"
                tickers = await self.run_trigger_batch(mode, exclude_codes=exclude_codes)

                logger.info(f"Tickers returned from trigger batch: {tickers}")

                # 2. If tickers count is less than 3, run custom screener to fill up
                if len(tickers) < 3:
                    logger.info(f"Selected triggers returned only {len(tickers)} stocks. Running custom screener...")
                    try:
                        import pandas as pd
                        import screener
                        from trigger_batch import score_candidates_by_agent_criteria, get_nearest_business_day_in_a_week
                        
                        # Fetch candidates from both KOSPI and KOSDAQ
                        logger.info("Executing custom screener for KOSPI and KOSDAQ...")
                        cands = []
                        for market in ["KOSPI", "KOSDAQ"]:
                            try:
                                # Query top_n = 3 in screener to have enough fallback pool
                                df_cand = screener.get_candidates(market, 3)
                                if df_cand is not None and not df_cand.empty:
                                    cands.append(df_cand)
                            except Exception as se:
                                logger.error(f"Screener failed for {market}: {str(se)}")
                                
                        if cands:
                            raw_screen_df = pd.concat(cands, ignore_index=True)
                        else:
                            raw_screen_df = pd.DataFrame()
                            
                        if not raw_screen_df.empty:
                            # Convert to index-based DataFrame for scoring, ensuring 'Code' is the index
                            raw_screen_df.set_index('Code', inplace=True, drop=False)
                            
                            # Filter out portfolio stocks from custom screener candidates
                            if exclude_codes:
                                raw_screen_df = raw_screen_df[~raw_screen_df['Code'].isin(exclude_codes)]
                            
                            # Fetch trade date
                            today_str = datetime.now().strftime("%Y%m%d")
                            trade_date = get_nearest_business_day_in_a_week(today_str, prev=True)
                            
                            logger.info(f"Scoring custom screener candidates using agent criteria (reference date: {trade_date})...")
                            scored_screener_df = score_candidates_by_agent_criteria(
                                raw_screen_df, trade_date, lookback_days=10, trigger_type="default"
                            )
                            
                            # Partition into valid and invalid (fallback) candidates
                            valid_screener_df = scored_screener_df[scored_screener_df["agent_fit_score"] > 0.0]
                            invalid_screener_df = scored_screener_df[scored_screener_df["agent_fit_score"] <= 0.0]
                            
                            logger.info(f"Custom screen candidates - Valid: {len(valid_screener_df)}, Invalid: {len(invalid_screener_df)}")
                            
                            # Select valid ones first
                            selected_screeners = []
                            if not valid_screener_df.empty:
                                for ticker in valid_screener_df.index:
                                    row = valid_screener_df.loc[ticker]
                                    selected_screeners.append({
                                        'code': row['Code'],
                                        'name': row['Name'],
                                        'current_price': float(row['Close']) if 'Close' in row else 0.0,
                                        'change_rate': float(row['ChangesRatio']) if 'ChangesRatio' in row else 0.0,
                                        'volume': int(row['Volume']) if 'Volume' in row else 0,
                                        'trade_value': float(row['Amount']) if 'Amount' in row else 0.0,
                                        'volume_profile_info': str(row['volume_profile_info']) if 'volume_profile_info' in row else "No significant upper resistance",
                                        'risk_reward_ratio': float(row['risk_reward_ratio']) if 'risk_reward_ratio' in row else 0.0,
                                        'stop_loss_pct': float(row['stop_loss_pct']) * 100 if 'stop_loss_pct' in row else 0.0,
                                        'stop_loss_price': float(row['stop_loss_price']) if 'stop_loss_price' in row else 0.0,
                                        'target_price': float(row['target_price']) if 'target_price' in row else 0.0,
                                        'pivot_point': float(row['pivot_point']) if 'pivot_point' in row else 0.0,
                                        'agent_fit_score': float(row['agent_fit_score']) if 'agent_fit_score' in row else 0.0,
                                        'is_fallback': False
                                    })
                                    
                            # Extend tickers list
                            existing_codes = {item['code'] for item in tickers}
                            filtered_screeners = [s for s in selected_screeners if s['code'] not in existing_codes]
                            for s in filtered_screeners:
                                s['trigger_type'] = "My Custom Screen"
                                s['trigger_mode'] = mode
                                
                            tickers.extend(filtered_screeners)
                            existing_codes.update({s['code'] for s in filtered_screeners})
                            
                            # If tickers is STILL less than 3, perform final fallback with screener's invalid candidates
                            if len(tickers) < 3 and not invalid_screener_df.empty:
                                # Sort invalid candidates by RS or ChangesRatio or Amount
                                sort_col = None
                                for col in ["RS", "ChangesRatio", "Amount", "Volume"]:
                                    if col in invalid_screener_df.columns:
                                        sort_col = col
                                        break
                                if sort_col:
                                    invalid_screener_df = invalid_screener_df.sort_values(sort_col, ascending=False)
                                
                                for ticker in invalid_screener_df.index:
                                    if ticker not in existing_codes and len(tickers) < 3:
                                        row = invalid_screener_df.loc[ticker]
                                        fallback_item = {
                                            'code': row['Code'],
                                            'name': row['Name'],
                                            'current_price': float(row['Close']) if 'Close' in row else 0.0,
                                            'change_rate': float(row['ChangesRatio']) if 'ChangesRatio' in row else 0.0,
                                            'volume': int(row['Volume']) if 'Volume' in row else 0,
                                            'trade_value': float(row['Amount']) if 'Amount' in row else 0.0,
                                            'volume_profile_info': str(row['volume_profile_info']) if 'volume_profile_info' in row else "No significant upper resistance",
                                            'risk_reward_ratio': float(row['risk_reward_ratio']) if 'risk_reward_ratio' in row else 0.0,
                                            'stop_loss_pct': float(row['stop_loss_pct']) * 100 if 'stop_loss_pct' in row else 0.0,
                                            'stop_loss_price': float(row['stop_loss_price']) if 'stop_loss_price' in row else 0.0,
                                            'target_price': float(row['target_price']) if 'target_price' in row else 0.0,
                                            'pivot_point': float(row['pivot_point']) if 'pivot_point' in row else 0.0,
                                            'agent_fit_score': float(row['agent_fit_score']) if 'agent_fit_score' in row else 0.0,
                                            'is_fallback': True,
                                            'trigger_type': "My Custom Screen (Fallback)",
                                            'trigger_mode': mode
                                        }
                                        tickers.append(fallback_item)
                                        existing_codes.add(ticker)
                                        logger.info(f"Custom Screen Fallback selection: {ticker}")
                                        
                            # Save all screener additions to results_file
                            all_additions = [t for t in tickers if t.get('trigger_type', '').startswith("My Custom Screen")]
                            if all_additions and os.path.exists(results_file):
                                try:
                                    with open(results_file, 'r', encoding='utf-8') as f:
                                        res_data = json.load(f)
                                    
                                    if "My Custom Screen" not in res_data:
                                        res_data["My Custom Screen"] = []
                                        
                                    existing_res_codes = {x['code'] for x in res_data["My Custom Screen"]}
                                    for item in all_additions:
                                        if item['code'] not in existing_res_codes:
                                            res_data["My Custom Screen"].append({
                                                "code": item['code'],
                                                "name": item['name'],
                                                "current_price": item.get('current_price', 0.0),
                                                "change_rate": item.get('change_rate', 0.0),
                                                "volume": item.get('volume', 0),
                                                "trade_value": item.get('trade_value', 0.0),
                                                "volume_profile_info": item.get('volume_profile_info', "No significant upper resistance"),
                                                "risk_reward_ratio": item.get('risk_reward_ratio', 0.0),
                                                "stop_loss_pct": item.get('stop_loss_pct', 0.0),
                                                "stop_loss_price": item.get('stop_loss_price', 0.0),
                                                "target_price": item.get('target_price', 0.0),
                                                "pivot_point": item.get('pivot_point', 0.0),
                                                "agent_fit_score": item.get('agent_fit_score', 0.0),
                                                "is_fallback": item.get('is_fallback', False)
                                            })
                                            
                                    with open(results_file, 'w', encoding='utf-8') as f:
                                        json.dump(res_data, f, ensure_ascii=False, indent=2)
                                    logger.info(f"Successfully added custom screen tickers to {results_file}")
                                except Exception as e:
                                    logger.error(f"Error saving custom screen tickers to {results_file}: {str(e)}")
                    except Exception as e:
                        logger.error(f"Error in custom screener processing: {str(e)}")
                
                # 3. Always slice tickers to exactly 3 to ensure we don't exceed the target count
                if len(tickers) > 3:
                    logger.info(f"Slicing tickers from {len(tickers)} to exactly 3 stocks")
                    tickers = tickers[:3]

                logger.info(f"Final tickers list: {tickers}")

                if not tickers:
                    await self.send_telegram_message_only(f"금일 {mode} 매매에 해당하는 종목이 없습니다.")
                    return

                # 1-1. Send trigger results to telegram immediately
                if os.path.exists(results_file):
                    logger.info(f"Trigger results file confirmed: {results_file}")
                    alert_sent = await self.send_trigger_alert(mode, results_file, language)
                    if alert_sent:
                        logger.info("Prism Signal alert transmission complete")
                    else:
                        logger.warning("Prism Signal alert transmission failed")
                else:
                    logger.warning(f"Trigger results file not found: {results_file}")

                # 2. Generate reports - important: await added here!
                report_paths = await self.generate_reports(tickers, mode, timeout=600, language=language)
                if not report_paths:
                    logger.warning("No reports generated. Terminating process.")
                    return

                # 3. PDF conversion
                pdf_paths = await self.convert_to_pdf(report_paths)

                # 4-5. Generate and send telegram messages (only when telegram is enabled)
                if self.telegram_config.use_telegram:
                    logger.info("Telegram enabled - proceeding with message generation and transmission steps")

                    # 4. Generate telegram messages
                    message_paths = await self.generate_telegram_messages(pdf_paths, language)

                    # 5. Send telegram messages and PDFs
                    await self.send_telegram_messages(message_paths, pdf_paths, report_paths)
                else:
                    logger.info("Telegram disabled - skipping message generation and transmission steps")

            # 6. Tracking system batch (runs concurrently with broadcast I/O tasks via async)
            if report_paths or is_portfolio_full:
                try:
                    logger.info("Starting stock tracking system batch execution")

                    # Import tracking agent
                    from stock_tracking_enhanced_agent import EnhancedStockTrackingAgent as StockTrackingAgent
                    from stock_tracking_agent import app as tracking_app

                    # Validate telegram configuration
                    if self.telegram_config.use_telegram:
                        # Validate required settings when telegram is enabled
                        try:
                            self.telegram_config.validate_or_raise()
                        except ValueError as ve:
                            logger.error(f"Telegram configuration error: {str(ve)}")
                            logger.error("Skipping tracking system batch.")
                            return

                    # Log telegram configuration status
                    self.telegram_config.log_status()

                    # Use MCPApp context manager
                    async with tracking_app.run():
                        # Pass telegram configuration to agent
                        tracking_agent = StockTrackingAgent(
                            telegram_token=self.telegram_config.bot_token if self.telegram_config.use_telegram else None
                        )

                        # Pass report paths, telegram configuration, and language
                        chat_id = self.telegram_config.channel_id if self.telegram_config.use_telegram else None

                        # Pass trigger results file for trigger_type tracking
                        trigger_results_file = f"trigger_results_{mode}_{datetime.now().strftime('%Y%m%d')}.json"
                        tracking_success = await tracking_agent.run(
                            report_paths, chat_id, language, self.telegram_config,
                            trigger_results_file=trigger_results_file
                        )

                        if tracking_success:
                            logger.info("Tracking system batch execution complete")
                        else:
                            logger.error("Tracking system batch execution failed")

                except Exception as e:
                    logger.error(f"Error during tracking system batch execution: {str(e)}")
                    import traceback
                    logger.error(traceback.format_exc())
            else:
                logger.warning("No reports generated, not executing tracking system batch.")

            logger.info(f"Full pipeline complete - mode: {mode}")

        except Exception as e:
            logger.error(f"Error during pipeline execution: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

        finally:
            # Always wait for background broadcast tasks, even on error/early return
            if self._broadcast_tasks:
                logger.info(f"Waiting for {len(self._broadcast_tasks)} broadcast translation task(s) to complete...")
                results = await asyncio.gather(*self._broadcast_tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"Broadcast task {i+1} failed: {result}")
                self._broadcast_tasks.clear()
                logger.info("All broadcast translation tasks completed")

    async def generate_reports(self, tickers, mode, timeout: int = None, language: str = "ko") -> list:
        """
        Generate reports serially for all stocks.
        Process one stock at a time to prevent OpenAI rate limit issues.

        Args:
            tickers: List of stocks to analyze
            mode: Execution mode
            timeout: Timeout (seconds)
            language: Analysis language ("ko" or "en")

        Returns:
            list: List of successful report paths
        """

        logger.info(f"Starting report generation for {len(tickers)} stocks (serial processing)")

        successful_reports = []

        # Process each stock sequentially
        for idx, ticker_info in enumerate(tickers, 1):
            # If ticker_info is a dict
            if isinstance(ticker_info, dict):
                ticker = ticker_info.get('code')
                # Use 'or' to handle both None and empty string cases
                company_name = ticker_info.get('name') or f"Stock_{ticker}"
                volume_profile_info = ticker_info.get('volume_profile_info', None)
            else:
                ticker = ticker_info
                company_name = f"Stock_{ticker}"
                volume_profile_info = None

            logger.info(f"[{idx}/{len(tickers)}] Starting stock analysis: {company_name}({ticker})")

            # Set output file path
            reference_date = datetime.now().strftime("%Y%m%d")
            output_file = str(REPORTS_DIR / f"{ticker}_{company_name}_{reference_date}_{mode}_gpt5.2.md")

            try:
                # Import function directly from main.py
                from cores.main import analyze_stock

                # Use await directly since already in async environment
                logger.info(f"[{idx}/{len(tickers)}] Starting analyze_stock function call")
                report = await analyze_stock(
                    company_code=ticker,
                    company_name=company_name,
                    reference_date=reference_date,
                    language=language,
                    volume_profile_info=volume_profile_info
                )

                # Save result
                if report and len(report.strip()) > 0:
                    with open(output_file, "w", encoding="utf-8") as f:
                        f.write(report)
                    logger.info(f"[{idx}/{len(tickers)}] Report generation complete: {company_name}({ticker}) - {len(report)} characters")
                    successful_reports.append(output_file)
                else:
                    logger.error(f"[{idx}/{len(tickers)}] Report generation failed: {company_name}({ticker}) - empty content")

            except Exception as e:
                logger.error(f"[{idx}/{len(tickers)}] Error during analysis: {company_name}({ticker}) - {str(e)}")
                import traceback
                logger.error(traceback.format_exc())


        logger.info(f"Report generation complete: {len(successful_reports)}/{len(tickers)} successful")

        return successful_reports

async def main():
    """
    Main function - command line interface
    """
    parser = argparse.ArgumentParser(description="Stock analysis and telegram transmission orchestrator")
    parser.add_argument("--mode", choices=["morning", "afternoon", "both"], default="both",
                        help="Execution mode (morning, afternoon, both)")
    parser.add_argument("--language", choices=["ko", "en"], default="ko",
                        help="Analysis language (ko: Korean, en: English)")
    parser.add_argument("--broadcast-languages", type=str, default="",
                        help="Additional languages for parallel telegram channel broadcasting (comma-separated, e.g., 'en,ja,zh')")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Disable telegram message transmission. "
                             "Use when testing without telegram configuration or running locally.")

    args = parser.parse_args()

    # Parse broadcast languages
    broadcast_languages = [lang.strip() for lang in args.broadcast_languages.split(",") if lang.strip()]

    # Create telegram configuration
    from telegram_config import TelegramConfig
    telegram_config = TelegramConfig(use_telegram=not args.no_telegram, broadcast_languages=broadcast_languages)

    # Validate telegram configuration (only when enabled)
    if telegram_config.use_telegram:
        try:
            telegram_config.validate_or_raise()
        except ValueError as e:
            logger.error(f"Telegram configuration error: {str(e)}")
            logger.error("Terminating program.")
            sys.exit(1)

    # Log telegram configuration status
    telegram_config.log_status()

    orchestrator = StockAnalysisOrchestrator(telegram_config=telegram_config)

    if args.mode == "morning" or args.mode == "both":
        await orchestrator.run_full_pipeline("morning", language=args.language)

    if args.mode == "afternoon" or args.mode == "both":
        await orchestrator.run_full_pipeline("afternoon", language=args.language)


async def hi():
    s = StockAnalysisOrchestrator()
    r = await s.generate_reports([{"code": "005930", "name": "삼성전자"}], "test", language="ko")

if __name__ == "__main__":
    # Check market holiday
    from check_market_day import is_market_day

    if not is_market_day():
        current_date = datetime.now().date()  # Use datetime.now()
        logger.info(f"Today ({current_date}) is a stock market holiday. Not executing batch job.")
        sys.exit(0)

    # Start timer thread and execute main function only on business days
    import threading

    # Timer function to terminate process after 120 minutes
    def exit_after_timeout():
        import time
        import os
        import signal
        time.sleep(10800)  # Wait 120 minutes
        logger.warning("180-minute timeout reached: forcefully terminating process")
        os.kill(os.getpid(), signal.SIGTERM)

    # Start timer as background thread
    timer_thread = threading.Thread(target=exit_after_timeout, daemon=True)
    timer_thread.start()

    asyncio.run(main())